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
USERSYNC_CONFIG_MAPPING = {
    "sync_interval": "SYNC_INTERVAL",
    "sync_ldap_url": "SYNC_LDAP_URL",
    "sync_ldap_bind_dn": "SYNC_LDAP_BIND_DN",
    "sync_ldap_bind_password": "SYNC_LDAP_BIND_PASSWORD",  # nosec B105
    "sync_ldap_deltasync": "SYNC_LDAP_DELTASYNC",
    "sync_ldap_search_base": "SYNC_LDAP_SEARCH_BASE",
    "sync_ldap_user_search_base": "SYNC_LDAP_USER_SEARCH_BASE",
    "sync_ldap_user_search_scope": "SYNC_LDAP_USER_SEARCH_SCOPE",
    "sync_ldap_user_object_class": "SYNC_LDAP_USER_OBJECT_CLASS",
    "sync_ldap_user_search_filter": "SYNC_LDAP_USER_SEARCH_FILTER",
    "sync_ldap_user_name_attribute": "SYNC_LDAP_USER_NAME_ATTRIBUTE",
    "sync_ldap_user_group_name_attribute": "SYNC_LDAP_USER_GROUP_NAME_ATTRIBUTE",
    "sync_group_search_enabled": "SYNC_GROUP_SEARCH_ENABLED",
    "sync_group_user_map_sync_enabled": "SYNC_GROUP_USER_MAP_SYNC_ENABLED",
    "sync_group_search_base": "SYNC_GROUP_SEARCH_BASE",
    "sync_ldap_group_search_scope": "SYNC_GROUP_SEARCH_SCOPE",
    "sync_group_object_class": "SYNC_GROUP_OBJECT_CLASS",
    "sync_group_member_attribute_name": "SYNC_GROUP_MEMBER_ATTRIBUTE_NAME",
}

# Observability literals
METRICS_PORT = 6080
LOG_FILES = ["/usr/lib/ranger/admin/ews/logs/ranger-admin-ranger-k8s-0-.log"]
SUPPRESS_DEBUG_LOGS = True

HEADERS = {
    "Content-Type": "application/json",
}

# OpenSearch literals
INDEX_NAME = "ranger_audits"
CERTIFICATE_NAME = "opensearch-ca"
OPENSEARCH_SCHEMA = {
    "properties": {
        "_expire_at_": {"type": "date", "store": True, "doc_values": True},
        "_ttl_": {"type": "text", "store": True},
        "_version_": {"type": "long", "store": True, "index": False},
        "access": {"type": "keyword"},
        "action": {"type": "keyword"},
        "agent": {"type": "keyword"},
        "agentHost": {"type": "keyword"},
        "cliIP": {"type": "keyword"},
        "cliType": {"type": "keyword"},
        "cluster": {"type": "keyword"},
        "reqContext": {"type": "keyword"},
        "enforcer": {"type": "keyword"},
        "event_count": {"type": "long", "doc_values": True},
        "event_dur_ms": {"type": "long", "doc_values": True},
        "evtTime": {"type": "date", "doc_values": True},
        "id": {"type": "keyword", "store": True},
        "logType": {"type": "keyword"},
        "policy": {"type": "long", "doc_values": True},
        "proxyUsers": {"type": "keyword"},
        "reason": {"type": "text"},
        "repo": {"type": "keyword"},
        "repoType": {"type": "integer", "doc_values": True},
        "req_caller_id": {"type": "keyword"},
        "req_self_id": {"type": "keyword"},
        "reqData": {"type": "text"},
        "reqUser": {"type": "keyword"},
        "resType": {"type": "keyword"},
        "resource": {"type": "keyword"},
        "result": {"type": "integer"},
        "seq_num": {"type": "long", "doc_values": True},
        "sess": {"type": "keyword"},
        "tags": {"type": "keyword"},
        "tags_str": {"type": "text"},
        "datasets": {"type": "keyword"},
        "projects": {"type": "keyword"},
        "text": {"type": "text"},
        "zoneName": {"type": "keyword"},
        "policyVersion": {"type": "long"},
    }
}
DEFAULT_POLICIES = [
    "all - trinouser",
    "all - catalog",
    "all - catalog, schema",
    "all - catalog, schema, procedure",
    "all - catalog, schema, schemafunction",
    "all - catalog, schema, table",
    "all - catalog, schema, table, column",
    "all - catalog, sessionproperty",
    "all - function",
    "all - queryid",
    "all - role",
    "all - sysinfo",
    "all - systemproperty",
]

# Trino catalog reconciliation literals
TRINO_SERVICE_TYPE = "trino"
DEFAULT_POLICY_SUFFIXES = ("ro", "rw", "ddl", "is")
ZONE_ROLE_SUFFIXES = ("-viewer", "-editor", "-admin", "-auditor")
