"""
Import every model module here so Base.metadata is fully populated for Alembic autogenerate
and for Base.metadata.create_all() in tests. Add new model files' imports here too.
"""
from app.models.action_token import ActionToken  # noqa: F401
from app.models.audit import AuditLog  # noqa: F401
from app.models.conversation import (  # noqa: F401
    Conversation,
    Message,
    MessageCitation,
    MessageFeedback,
)
from app.models.knowledge import (  # noqa: F401
    Chunk,
    Document,
    DocumentVersion,
    KnowledgeSource,
)
from app.models.organization import Membership, Organization, Role  # noqa: F401
from app.models.ticket import Customer, Ticket, TicketNote  # noqa: F401
from app.models.user import RefreshToken, User  # noqa: F401
