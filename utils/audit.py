from flask import current_app
from models.audit_log import AuditLog


def log_audit(identity, action, entity_type, entity_id, description=None):
    """Record an audit-trail entry. Never raises — a logging failure must
    never block the actual create/update/delete it's describing."""
    try:
        entry = AuditLog(
            user_id=identity.get('id') if identity else None,
            username=identity.get('username') if identity else None,
            role=identity.get('role') if identity else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            description=description
        )
        entry.save()
    except Exception as e:
        current_app.logger.warning('log_audit: failed to write audit log (%s %s %s): %s', action, entity_type, entity_id, e)
