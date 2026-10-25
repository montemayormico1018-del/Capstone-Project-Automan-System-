"""Firestore-backed data layer that mimics the SQLAlchemy API used across the app.

This lets existing route code keep calling ``Model.query.filter_by(...).first()``,
``db.session.add(obj)``, ``db.session.commit()``, etc., while storing data in
Cloud Firestore instead of SQLite.

Design notes:
- Each model maps to a Firestore *collection* named after ``__collection__``.
- Documents are identified by an ``id`` field. New objects get an auto ID on
  first commit unless an id is supplied.
- Relationships (FKs) are stored as plain string document IDs.
- Many-to-many association tables (appointment_services, job_order_services)
  are stored as ``list`` fields on the owning document (``service_ids``).
- The session tracks "pending" objects and writes them in ``commit()``.
"""
from datetime import datetime, date, time
import threading
import uuid

from firebase_config import get_db


# ---------------------------------------------------------------------------
# Value serialization helpers
# ---------------------------------------------------------------------------
def _serialize(value):
    # Firestore's client only knows how to encode full datetimes (as native
    # timestamps); bare date/time values have no Firestore type and raise
    # TypeError("Cannot convert to a Firestore Value", value, "Invalid type",
    # type(value)) if passed through as-is. Store them as ISO strings instead.
    # NOTE: datetime is a subclass of date, so it must be checked first.
    if isinstance(value, datetime):
        return value
    if isinstance(value, time):
        return value.strftime('%H:%M:%S')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(v) for v in value]
    return value


def _to_iso(value):
    """Convert a datetime/date/time to ISO string for JSON, else return as-is."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _to_iso(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_iso(v) for v in value]
    return value


def _deserialize(value):
    # Firestore returns datetime objects natively; nothing to do here.
    return value


# ---------------------------------------------------------------------------
# Query object (mimics sqlalchemy Query)
# ---------------------------------------------------------------------------
class Query:
    def __init__(self, model, collection):
        self._model = model
        self._collection = collection
        self._filters = []  # list of (field, op, value)
        self._order_by = None
        self._limit = None
        self._entities = None

    def _clone(self):
        # SQLAlchemy queries are immutable/chainable: query.filter_by(...) returns
        # a *new* query, so a caller can reuse a base query for several
        # independent .filter_by(...).count() calls. Mutating self here would
        # make each successive filter permanently stick to the shared query.
        clone = Query(self._model, self._collection)
        clone._filters = list(self._filters)
        clone._order_by = self._order_by
        clone._limit = self._limit
        clone._entities = self._entities
        return clone

    def filter_by(self, **kwargs):
        clone = self._clone()
        for field, value in kwargs.items():
            clone._filters.append((field, "==", value))
        return clone

    def filter(self, *exprs):
        # Support simple expressions via _Expr helper (field == value, etc.)
        clone = self._clone()
        for expr in exprs:
            if isinstance(expr, _Expr):
                clone._filters.append((expr.field, expr.op, expr.value))
        return clone

    def order_by(self, expr):
        # expr is like Model.created_at (desc supported via .desc())
        clone = self._clone()
        field = getattr(expr, "field", None) or getattr(expr, "_firestore_field", None)
        direction = getattr(expr, "direction", "ASCENDING")
        clone._order_by = (field, direction)
        return clone

    def limit(self, n):
        clone = self._clone()
        clone._limit = n
        return clone

    def with_entities(self, *fields):
        # Firestore doesn't project like SQL; emulate by returning Row objects
        # with the requested scalar fields (supports attribute access).
        clone = self._clone()
        clone._entities = [getattr(f, "field", None) or str(f) for f in fields]
        return clone

    def _apply(self, query):
        from google.cloud.firestore import FieldFilter
        for field, op, value in self._filters:
            # Same date/time encoding gap as document writes: a bare
            # date/time filter value would otherwise raise TypeError when
            # Firestore tries to encode it (see _serialize()).
            query = query.where(filter=FieldFilter(field, op, _serialize(value)))
        if self._order_by:
            field, direction = self._order_by
            if direction == "DESCENDING":
                query = query.order_by(field, direction=firestore_ORDER_DESC)
            else:
                query = query.order_by(field)
        if self._limit is not None:
            query = query.limit(self._limit)
        return query

    def _build(self):
        q = self._collection
        q = self._apply(q)
        return q.stream()

    def all(self):
        results = []
        for doc in self._build():
            if self._entities:
                d = doc.to_dict() or {}
                results.append(Row({f: d.get(f) for f in self._entities}))
            else:
                results.append(self._model._from_doc(doc))
        return results

    def first(self):
        results = self.all()
        return results[0] if results else None

    def count(self):
        # Firestore has no native count without aggregate; approximate via read.
        return len(self.all())

    def get(self, doc_id):
        doc = self._collection.document(str(doc_id)).get()
        if doc.exists:
            return self._model._from_doc(doc)
        return None


class Row:
    """Lightweight result row (used by with_entities); attribute + item access."""
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):
        return self._data.get(name)

    def __getitem__(self, key):
        return self._data.get(key)

    def __repr__(self):
        return f"<Row {self._data}>"


# Expression helper for .filter(Model.x == y) and order_by(Model.x.desc())
class _Expr:
    def __init__(self, field, op="==", value=None):
        self.field = field
        self.op = op
        self.value = value
        self.direction = "ASCENDING"

    def desc(self):
        self.direction = "DESCENDING"
        return self

    def in_(self, values):
        return _Expr(self.field, "in", list(values))

    def __eq__(self, other):
        return _Expr(self.field, "==", other)

    def __ne__(self, other):
        return _Expr(self.field, "!=", other)

    def __lt__(self, other):
        return _Expr(self.field, "<", other)

    def __le__(self, other):
        return _Expr(self.field, "<=", other)

    def __gt__(self, other):
        return _Expr(self.field, ">", other)

    def __ge__(self, other):
        return _Expr(self.field, ">=", other)


def field_expr(name):
    """Return a field expression usable in filter/order_by, e.g. Model.id."""
    return _Expr(name)


# ---------------------------------------------------------------------------
# Model base
# ---------------------------------------------------------------------------
class _ModelMeta(type):
    """Metaclass so that ``Model.fieldname`` (on the class) returns a query
    expression, mirroring SQLAlchemy's instrumented attributes."""
    def __getattr__(cls, name):
        return _Expr(name)


class DocumentModel(metaclass=_ModelMeta):
    __collection__ = None  # must be set by subclasses

    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", None)
        for key, value in kwargs.items():
            setattr(self, key, value)

    # ----- persistence -----
    @classmethod
    def _col(cls):
        return get_db().collection(cls.__collection__)

    @classmethod
    def _from_doc(cls, doc):
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        obj = cls(id=doc.id, **data)
        # Auto-track loaded objects with the session, mirroring real
        # SQLAlchemy's identity map: db.session.commit() must persist
        # mutations made to an object fetched via Model.query.get()/.all()/
        # etc even when the caller never explicitly called db.session.add()
        # on it. Without this, the dominant "fetch, mutate a field, commit"
        # update pattern used across routes/*.py silently no-ops.
        _session.add(obj)
        return obj

    def to_dict(self):
        """Override in subclasses to control serialized output."""
        out = {}
        for k, v in self.__dict__.items():
            if k.startswith("_") or k == "id":
                continue
            out[k] = _to_iso(v)
        if self.id is not None:
            out["id"] = self.id
        return out

    def _doc_fields(self):
        """Fields stored in the Firestore document (excludes id and relationships)."""
        return {k: _serialize(v) for k, v in self.__dict__.items()
                if not k.startswith("_") and k != "id"}

    @classmethod
    def get(cls, doc_id):
        return cls.query.get(doc_id)

    @classmethod
    def get_many(cls, doc_ids):
        """Batch-fetch several documents by id in one round trip.

        Document ids aren't stored as a queryable field (``_doc_fields``
        excludes ``id``), so ``query.filter(Model.id.in_(...))`` can't be
        used to batch these — Firestore's own multi-get (``get_all``) is
        the real primitive. This is what turns list endpoints that serialize
        many related documents (e.g. an appointment's customer/vehicle/
        services) from one round trip per document into one round trip per
        batch of up to a few hundred.
        """
        ids = list({str(i) for i in doc_ids if i})
        if not ids:
            return {}
        col = cls._col()
        db = get_db()
        result = {}
        if hasattr(db, "get_all"):
            refs = [col.document(i) for i in ids]
            for doc in db.get_all(refs):
                if doc.exists:
                    result[doc.id] = cls._from_doc(doc)
        else:
            # Mock/test backends without get_all: fall back to individual
            # gets (correct either way, just not batched).
            for i in ids:
                obj = cls.get(i)
                if obj is not None:
                    result[i] = obj
        return result

    def save(self, merge=True):
        col = self._col()
        if self.id is None:
            doc_ref = col.document()
            self.id = doc_ref.id
        else:
            doc_ref = col.document(str(self.id))
        doc_ref.set(self._doc_fields(), merge=merge)
        return self

    def delete(self):
        if self.id is not None:
            self._col().document(str(self.id)).delete()

    # ----- handy classmethods used in code -----
    @classmethod
    def find_by(cls, **kwargs):
        return cls.query.filter_by(**kwargs).first()

    @classmethod
    def list_all(cls):
        return cls.query.all()


class _QueryDescriptor:
    """Descriptor that makes ``Model.query`` return a fresh Query instance
    whether accessed on the class or an instance (mirrors SQLAlchemy)."""
    def __get__(self, instance, owner):
        if owner is None:
            owner = type(instance)
        return Query(owner, owner._col())


DocumentModel.query = _QueryDescriptor()


# ---------------------------------------------------------------------------
# Session emulator
# ---------------------------------------------------------------------------
class Session:
    """gunicorn runs this app with --threads 4, so multiple requests can be
    live in the same worker process at once, sharing this module's global
    state. A single shared _pending/_deleted list let one request's
    rollback() (or an interleaved commit() firing before this request's own)
    silently wipe another in-flight request's not-yet-saved object -- it
    would keep its id=None forever, no exception raised, 201 still returned
    to the client, and the record was simply never written to Firestore.
    Backing pending/deleted by thread-local storage gives each request
    thread its own isolated queue, matching gunicorn's one-thread-per-request
    model and eliminating the cross-request race entirely."""
    def __init__(self):
        self._local = threading.local()

    @property
    def _pending(self):
        if not hasattr(self._local, 'pending'):
            self._local.pending = []
        return self._local.pending

    @property
    def _deleted(self):
        if not hasattr(self._local, 'deleted'):
            self._local.deleted = []
        return self._local.deleted

    def add(self, obj):
        if obj not in self._pending:
            self._pending.append(obj)

    def delete(self, obj):
        self._deleted.append(obj)

    def commit(self):
        for obj in self._pending:
            obj.save(merge=True)
        for obj in self._deleted:
            obj.delete()
        self._pending.clear()
        self._deleted.clear()

    def rollback(self):
        self._pending.clear()
        self._deleted.clear()

    def flush(self):
        # Firestore writes immediately; flush just commits pending objects so
        # relationships that depend on generated ids resolve.
        self.commit()

    def execute(self, stmt, *args, **kwargs):
        raise NotImplementedError(
            "Raw SQL execution is not supported on Firestore. "
            "Use list fields (e.g. service_ids) instead."
        )


class FakeDB:
    """Drop-in replacement for flask_sqlalchemy's ``db`` object."""

    def init_app(self, app):
        # No initialization required for Firestore; kept for compatibility.
        self.app = app

    @property
    def session(self):
        return _session

    @property
    def Model(self):
        return DocumentModel

    def Column(self, *args, **kwargs):  # pragma: no cover - shim
        return None

    def relationship(self, *args, **kwargs):  # pragma: no cover - shim
        return None

    def ForeignKey(self, *args, **kwargs):  # pragma: no cover - shim
        return None

    def text(self, *args, **kwargs):  # pragma: no cover - shim
        raise NotImplementedError("Raw SQL text() is not supported on Firestore.")


# Late import to avoid circular import at module load; firestore ordering const
import os as _os
if _os.environ.get("FIRESTORE_MOCK") == "1":
    import mock_firestore as _fs_const
    firestore_ORDER_DESC = _fs_const.DESCENDING
else:
    from google.cloud import firestore as _fs_const
    firestore_ORDER_DESC = _fs_const.Query.DESCENDING

_session = Session()
db = FakeDB()
