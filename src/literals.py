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
RELATION_VALUES = [
    "sync_ldap_bind_dn",
    "sync_ldap_bind_password",
    "sync_ldap_search_base",
    "sync_ldap_user_search_base",
    "sync_group_search_base",
    "sync_ldap_url",
]

JAVA_ENV = {
    "JAVA_HOME": "/usr/lib/jvm/java-11-openjdk-amd64",
    "PATH": "$JAVA_HOME/bin:$PATH",
}

# Observability literals
METRICS_PORT = 6080
LOG_FILES = ["/usr/lib/ranger/admin/ews/logs/ranger-admin-ranger-k8s-0-.log"]
