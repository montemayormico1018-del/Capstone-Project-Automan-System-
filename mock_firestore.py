"""In-memory Firestore mock used for offline testing of the data layer.

It implements just enough of the ``google.cloud.firestore`` client API used by
this project (set/get/update/delete on documents, collection.stream(), and
query where/order_by/limit) to exercise the migration code without a real
Firebase project.

Activate by setting the env var FIRESTORE_MOCK=1 before importing firebase_config.
"""
import threading


class _FieldFilter:
    def __init__(self, field, op, value):
        self.field = field
        self.op = op
        self.value = value

    def match(self, data):
        if self.field not in data:
            return False
        v = data[self.field]
        if self.op == "==":
            return v == self.value
        if self.op == "!=":
            return v != self.value
        if self.op == "<":
            return v < self.value
        if self.op == "<=":
            return v <= self.value
        if self.op == ">":
            return v > self.value
        if self.op == ">=":
            return v >= self.value
        if self.op == "in":
            return v in self.value
        return False


class _Query:
    def __init__(self, collection, filters=None, orders=None, limit=None):
        self._collection = collection
        self._filters = filters or []
        self._orders = orders or []
        self._limit = limit

    def where(self, field=None, op=None, value=None, filter=None):
        if filter is not None:
            field, op, value = filter.field_path, filter.op_string, filter.value
        return _Query(self._collection, self._filters + [_FieldFilter(field, op, value)],
                      self._orders, self._limit)

    def order_by(self, field, direction=None):
        self._orders.append((field, "DESCENDING" == (direction or "ASCENDING")))
        return self

    def limit(self, n):
        return _Query(self._collection, self._filters, self._orders, n)

    def with_entities(self, *fields):
        q = _Query(self._collection, self._filters, self._orders, self._limit)
        q._entities = [getattr(f, "field", None) or str(f) for f in fields]
        return q

    def stream(self):
        results = []
        for doc in self._collection._docs.values():
            data = doc.to_dict() or {}
            if all(f.match(data) for f in self._filters):
                results.append(doc)
        for field, desc in self._orders:
            results.sort(key=lambda d: (d.to_dict() or {}).get(field), reverse=desc)
        if self._limit is not None:
            results = results[: self._limit]
        if getattr(self, "_entities", None):
            return iter([_EntityRow({f: (r.to_dict() or {}).get(f) for f in self._entities}) for r in results])
        return iter([_DocumentSnapshot(r) for r in results])


class _EntityRow:
    """Result row from with_entities(); supports both attr and key access."""
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):
        return self._data.get(name)

    def __getitem__(self, key):
        return self._data.get(key)

    def __repr__(self):
        return f"<EntityRow {self._data}>"


class _DocumentSnapshot:
    def __init__(self, doc):
        self._doc = doc

    @property
    def exists(self):
        return True

    @property
    def reference(self):
        return _DocumentRef(self._doc._collection, self._doc._id)

    def to_dict(self):
        return dict(self._doc._data)

    @property
    def id(self):
        return self._doc._id


class _DocumentRef:
    def __init__(self, collection, doc_id):
        self._collection = collection
        self._id = doc_id

    @property
    def id(self):
        return self._id

    def set(self, data, merge=False):
        existing = self._collection._docs.get(self._id)
        if existing is None:
            existing = _Document(self._collection, self._id)
            self._collection._docs[self._id] = existing
        if merge and existing._data:
            existing._data.update(data)
        else:
            existing._data = dict(data)
        return None

    def update(self, data):
        existing = self._collection._docs.get(self._id)
        if existing is None:
            existing = _Document(self._collection, self._id)
            self._collection._docs[self._id] = existing
        existing._data.update(data)
        return None

    def get(self):
        existing = self._collection._docs.get(self._id)
        if existing is None:
            return _MissingSnapshot(self._id)
        return _DocumentSnapshot(existing)

    def delete(self):
        self._collection._docs.pop(self._id, None)
        return None


class _MissingSnapshot:
    def __init__(self, doc_id):
        self._id = doc_id

    @property
    def exists(self):
        return False

    def to_dict(self):
        return None

    @property
    def id(self):
        return self._id


class _Document:
    def __init__(self, collection, doc_id):
        self._collection = collection
        self._id = doc_id
        self._data = {}

    @property
    def exists(self):
        return True

    def to_dict(self):
        return dict(self._data)

    @property
    def reference(self):
        return _DocumentRef(self._collection, self._id)


class _Collection:
    def __init__(self, client, name):
        self._client = client
        self._name = name
        self._docs = client._store.setdefault(name, {})

    def document(self, doc_id=None):
        if doc_id is None:
            import uuid
            doc_id = uuid.uuid4().hex
        return _DocumentRef(self, str(doc_id))

    def stream(self):
        return iter([_DocumentSnapshot(d) for d in self._docs.values()])

    def where(self, field=None, op=None, value=None, filter=None):
        return _Query(self).where(field=field, op=op, value=value, filter=filter)

    def order_by(self, field, direction=None):
        return _Query(self).order_by(field, direction)

    def limit(self, n):
        return _Query(self).limit(n)


class _Transaction:
    """Stand-in for ``google.cloud.firestore_v1.transaction.Transaction``.

    Mirrors the real API's context-manager form::

        with client.transaction() as tx:
            snap = tx.get(ref)
            tx.update(ref, {...})

    The client's lock is held for the whole ``with`` block and released on
    exit (including on exception), so transactions are fully serialized and
    each one reads the state left behind by the one before it. That is what
    makes a read-modify-write pair -- the shape every points balance update
    takes -- safe here exactly as it is against real Firestore: without the
    lock held across the block, two threads could both read balance=0, both
    decide they may spend 100, and both write, overdrawing.
    """

    def __init__(self, client):
        self._client = client
        self._writes = []  # (ref, data) replayed on commit
        self._active = False

    def __enter__(self):
        self._client._lock.acquire()
        self._active = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self._active = False
        self._client._lock.release()
        return False

    def get(self, ref):
        # Reads go straight to the store; the lock is already held so this
        # sees every write committed by a previous transaction.
        return ref.get()

    def set(self, ref, data, merge=False):
        if not self._active:
            raise RuntimeError("transaction write outside of a 'with' block")
        self._writes.append(('set', ref, data, merge))
        return None

    def update(self, ref, data):
        if not self._active:
            raise RuntimeError("transaction write outside of a 'with' block")
        self._writes.append(('update', ref, data, False))
        return None

    def delete(self, ref):
        if not self._active:
            raise RuntimeError("transaction write outside of a 'with' block")
        self._writes.append(('delete', ref, None, False))
        return None

    def _commit(self):
        for op, ref, data, merge in self._writes:
            if op == 'set':
                ref.set(data, merge=merge)
            elif op == 'update':
                ref.update(data)
            else:
                ref.delete()
        self._writes = []


class _TransactionContext:
    """``client.transaction()`` -> context manager applying writes on exit.

    The real SDK's context-manager form commits the accumulated writes when
    the block exits without raising, and discards them if it raises -- which
    is the behaviour a business-logic failure inside a points transaction
    must have.
    """

    def __init__(self, client):
        self._client = client

    def __enter__(self):
        self._tx = _Transaction(self._client)
        return self._tx.__enter__()

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._tx._commit()
        finally:
            self._tx.__exit__(exc_type, exc, tb)
        return False


class _MockClient:
    def __init__(self):
        self._store = {}
        self._lock = threading.Lock()

    def collection(self, name):
        return _Collection(self, name)

    def transaction(self):
        return _TransactionContext(self)

    def run_transaction(self, func, *args, **kwargs):
        """Callback form, matching the real client's run_transaction()."""
        for attempt in range(3):
            tx = _Transaction(self)
            with tx:
                result = func(tx, *args, **kwargs)
                tx._commit()
                return result
        raise RuntimeError("run_transaction: too many contended attempts")


ArrayUnion = lambda values: {"__array_union__": list(values)}
DESCENDING = "DESCENDING"


def get_client():
    return _MockClient()
