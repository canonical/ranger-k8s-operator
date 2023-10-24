# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling charm literals."""

APPLICATION_PORT = 6080
RANGER_URL = "http://localhost:6080"
ADMIN_USER = "admin"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}
SYSTEM_GROUPS = ["public"]
EXPECTED_KEYS = ["users", "groups", "memberships"]
MEMBER_TYPE_MAPPING = {
    "user": "vXUsers",
    "group": "vXGroups",
    "membership": "vXGroupUsers",
}
ENDPOINT_MAPPING = {
    "user": "users",
    "group": "groups",
    "membership": "groupusers",
}
