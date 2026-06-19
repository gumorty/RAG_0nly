from app.api.deps import can_read_acl
from app.core.security import hash_api_key, verify_api_key
from app.models.entities import User, UserRole


def test_api_key_hash_verification() -> None:
    digest = hash_api_key("secret-key")
    assert digest != "secret-key"
    assert verify_api_key("secret-key", digest)
    assert not verify_api_key("wrong-key", digest)


def test_acl_allows_admin_and_public() -> None:
    admin = User(id="admin-id", email="admin@example.com", name="Admin", role=UserRole.admin)
    viewer = User(id="viewer-id", email="viewer@example.com", name="Viewer", role=UserRole.viewer)
    assert can_read_acl(admin, ["private-user"])
    assert can_read_acl(viewer, ["public"])


def test_acl_allows_user_id_email_and_role() -> None:
    member = User(id="member-id", email="member@example.com", name="Member", role=UserRole.member)
    assert can_read_acl(member, ["member-id"])
    assert can_read_acl(member, ["member@example.com"])
    assert can_read_acl(member, ["member"])
    assert not can_read_acl(member, ["other-user"])
