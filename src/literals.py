# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling charm literals."""

APPLICATION_PORT = 6080
LOCALHOST_URL = "http://localhost"
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
APP_NAME = "ranger-k8s"
ADMIN_ENTRYPOINT = "/home/ranger/scripts/ranger-admin-entrypoint.sh"
USERSYNC_ENTRYPOINT = "/home/ranger/scripts/ranger-usersync-entrypoint.sh"
