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

JAVA_HOME = "/usr/lib/jvm/java-11-openjdk-amd64"

# Observability literals
METRICS_PORT = 6080
LOG_FILES = ["/usr/lib/ranger/admin/ews/logs/ranger-admin-ranger-k8s-0-.log"]

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
    "all - function",
    "all - catalog, sessionproperty",
    "all - catalog, schema, procedure",
    "all - catalog, schema, table",
    "all - systemproperty",
    "all - catalog, schema, table, column",
    "all - catalog, schema",
]
