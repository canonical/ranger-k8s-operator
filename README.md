<!--
Avoid using this README file for information that is maintained or published elsewhere, e.g.:

* metadata.yaml > published on Charmhub
* documentation > published on (or linked to from) Charmhub
* detailed contribution guide > documentation or CONTRIBUTING.md

Use links instead.
-->

# Ranger K8s Operator

This is the Kubernetes Python Operator for [Apache Ranger](https://github.com/apache/ranger).

## Usage

Note: This operator requires the use of juju>=3.1.

### Deploying Ranger and PostgreSQL Database in MicroK8S

Ranger requires PostgreSQL to store its state. 
Therefore, its deployment requires a relation with the Postgres charm:

Before deploying Ranger, create a Juju secret containing passwords for the
internal `admin` and `rangerusersync` accounts. Each password must be at least
eight characters long, include uppercase, lowercase, and numeric characters,
and must not contain `"`, `'`, `\`, or `` ` ``.

Set a restrictive file-creation mask, then create `system-users.yaml` with these
keys using a secure editor:

```shell
umask 077
```

```yaml
admin: <admin-password>
rangerusersync: <rangerusersync-password>
```

Create the secret from the file and remove the file:

```shell
SYSTEM_USERS=$(juju add-secret system-users --file=system-users.yaml)
rm system-users.yaml
```

```bash
juju deploy ranger-k8s
juju grant-secret system-users ranger-k8s
juju config ranger-k8s system-users="$SYSTEM_USERS"
juju deploy postgresql-k8s --channel 14/stable --trust
juju relate ranger-k8s:db postgresql-k8s:database
```
Refer to [CONTRIBUTING.md](./CONTRIBUTING.md) for details on bootstrapping a juju controller for microk8s.

### Group management with Apache Ranger
The Charmed Ranger Operator makes use of [Ranger usersync](https://cwiki.apache.org/confluence/display/RANGER/Apache+Ranger+Usersync) to synchronize users, groups and memberships from a compatible LDAP server (eg. openldap, ActiveDirectory) to Ranger admin. The usersync functionality can be configured on deployment of the Ranger Charm. While you can scale the Ranger admin application, you should only have 1 Usersync deployed.

```shell
juju deploy ranger-k8s ranger-usersync-k8s \
  --config charm-function=usersync \
  --config policy-mgr-url=http://ranger-k8s.<model>.svc.cluster.local:6080
juju grant-secret system-users ranger-usersync-k8s
juju config ranger-usersync-k8s system-users="$SYSTEM_USERS"

juju deploy comsys-openldap-k8s --channel=edge
juju relate ranger-usersync-k8s comsys-openldap-k8s
```
Usersync requires `policy-mgr-url`. It also requires either an LDAP relation or
an LDAP bind-identity secret and LDAP topology configuration. When using an
external LDAP server, create an `ldap-credentials` secret containing
`sync-ldap-bind-dn` and `sync-ldap-bind-password`, grant it to the usersync
application, and set its ID through `ldap-credentials`. Set `sync-ldap-url` and
`sync-ldap-search-base` as ordinary configuration options.

#### Group management in related application
Related applications must have the Ranger plugin configured. The Ranger plugin schedules regular download of Ranger policies (every 3 minutes) storing these policies within the related application in a cache. On access request, the requesting user's group is used when comparing to Ranger group policies to determine access. Therefore the related application should have the same source for groups.

#### Service name
Before relation of an application to the Ranger charm, the application's `ranger-service-name` configuration parameter should be set. This will be the name of the Ranger service created for the application.

### Integrating with Trino

#### `policy` interface

The `policy` interface enables Trino to download Ranger policies via the Ranger plugin. The configuration of groups is done automatically on relation with the Ranger charm in the [Trino K8s charm](https://charmhub.io/trino-k8s).

```bash
juju relate trino-k8s:policy ranger-k8s:policy
```

Confirm the applications are related and wait until active:

```bash
juju status --relations
```

Provide the Ranger configuration file:

```bash
juju config ranger-k8s --file=user-group-configuration.yaml
```

#### `trino-catalog` interface

The `trino-catalog` interface creates Ranger access-control resources for Trino
catalogs. When related, Ranger creates missing security zones, roles, and
default policies based on the catalogs exposed by Trino. Existing completed
Ranger objects are not updated or deleted.

##### Usage

Since there are multiple relations between Trino and Ranger, you must explicitly specify the endpoints:

```bash
juju relate trino-k8s:trino-catalog ranger-k8s:trino-catalog
```

The Ranger charm assumes there is a single registered Trino service on Ranger that belongs to the provider of the `trino-catalog` interface. It is highly recommended to only create this relation on a Trino charm already related on the `policy` interface.

##### How it works

Trino typically exposes a read-only catalog (such as `my_db`) and sometimes a read-write developer catalog (such as `my_db_developer`). When Ranger receives this catalog information, it performs the following automated setup:

1. **Security zones**: Ranger creates a dedicated security zone for each base catalog (grouping `<catalog>` and `<catalog>_developer` into a single zone named `<catalog>`).
2. **Roles**: Within the zone, Ranger automatically provisions four default roles for access management:
   - `<catalog>-viewer`
   - `<catalog>-editor`
   - `<catalog>-admin`
   - `<catalog>-auditor`
3. **Default policies**: Ranger configures default policies linking these roles to the appropriate permissions:
   - **Read-only (`ro`)**: Grants the `viewer`, `editor`, and `admin` roles Select, Show, and Use permissions on the base `<catalog>`.
   - **Read-write (`rw`)**: Grants the `editor` and `admin` roles Select, Show, Use, Insert, and Delete permissions on the `<catalog>_developer` catalog.
   - **DDL (`ddl`)**: Grants the `admin` role Alter, Create, and Drop permissions on the `<catalog>_developer` catalog.
   - **Information schema (`is`)**: Grants standard users Select, Show, and Use permissions on the `information_schema` for both catalogs.

After creation, the charm leaves completed zones, roles, and policies
unchanged. You can add custom policies to a generated zone without the charm
overwriting them.

##### Reconciliation configuration

Catalog reconciliation is create-only: the charm provisions missing zones,
roles, and default policies, but never updates or deletes operator-managed
state.

Strict reconciliation is enabled by default. For each default policy, the
charm omits every referenced role that already has members. This per-role
filtering ensures the charm never grants access through its own action. Set
`enforce-strict-reconciliation=false` only as an authorized security opt-out;
it restores unfiltered default policies.

Partial policies are expected. For example, if `<catalog>-admin` already has
members, the `ro` policy still grants `<catalog>-viewer` and
`<catalog>-editor`, but omits `<catalog>-admin`. If every role referenced by a
policy already has members, the charm creates an audit-only shell: it has empty
grants (`policyItems`), retains scoped resources, and keeps auditing enabled.

Zones are always finalized: the charm purges auto-policies and marks the zone
done even when some or all default policies become shells. A zone can therefore
remain bare and locked down, and the charm will not revisit it.

Role membership is evaluated from a provisioning snapshot. Roles populated
after provisioning still gain access through existing policies. Roles populated
at provisioning do not receive omitted default grants later, even if they are
subsequently emptied.

##### Managing access

As a Ranger administrator, **you do not need to manually create base policies for Trino catalogs**.

To grant a user or group access to a specific catalog:
1. Log into the Apache Ranger UI.
2. Navigate to **Settings > Roles**.
3. Edit the automatically generated role that matches the desired access level (such as `<catalog>-viewer` or `<catalog>-editor`).
4. Add the target users or groups to that role.

You are free to add custom policies to the generated security zones if you require more granular access control (for example, row-level filtering or column masking).

The default information-schema policy is an exception to role-based access:
its `{USER}` grant provides `information_schema` access even when the generated
roles are empty.

##### Catalog removal

Removing a catalog from Trino or removing the relation does not delete Ranger
objects. Remove the zone, roles, and policies manually when appropriate. To
revoke access, empty the generated roles or add deny policies rather than
deleting a zone: a missing zone can make its catalog subject to permissive
global Ranger policies.

### Charmed OpenSearch relation
[Charmed OpenSearch](https://charmhub.io/opensearch) should be integrated with the Ranger admin charm to enable auditing functionality for data access.

Charmed OpenSearch is a machine charm, unlike Charmed Ranger which is a K8s charm. As such we will need to bootstrap a LXD controller and implement a cross-controller relation. This can be achieved by:

```
# Bootstrap a LXD controller
juju bootstrap lxd lxd-controller

# Add a Model for OpenSearch
juju add-model opensearch

# Configure system settings of the host (required by OpenSearch)
cat <<EOF > cloudinit-userdata.yaml
cloudinit-userdata: |
  postruncmd:
    - [ 'echo', 'vm.max_map_count=262144', '>>', '/etc/sysctl.conf' ]
    - [ 'echo', 'vm.swappiness=0', '>>', '/etc/sysctl.conf' ]
    - [ 'echo', 'net.ipv4.tcp_retries2=5', '>>', '/etc/sysctl.conf' ]
    - [ 'echo', 'fs.file-max=1048576', '>>', '/etc/sysctl.conf' ]
    - [ 'sysctl', '-p' ]
EOF

sudo tee -a /etc/sysctl.conf > /dev/null <<EOT
vm.max_map_count=262144
vm.swappiness=0
net.ipv4.tcp_retries2=5
fs.file-max=1048576
EOT

sudo sysctl -p

juju model-config --file=./cloudinit-userdata.yaml

# Deploy OpenSearch
juju deploy ch:opensearch --channel=2/edge

# Deploy self-signed-certificates operator for enabling TLS
juju deploy self-signed-certificates --channel=latest/stable

# Enable TLS via relation
juju integrate self-signed-certificates opensearch

# Scale OpenSearch to 3 units
juju add-unit opensearch -n 2

# Offer the `opensearch-client` endpoint for consumption
juju offer opensearch:opensearch-client

# Switch back to your K8s controller and consume offer
juju switch ranger-controller
juju consume lxd-controller:admin/opensearch.opensearch

# Finally, relate the applications
juju relate ranger-k8s opensearch
```

More details on the setup process can be found [here](https://charmhub.io/opensearch/docs/t-overview).

### Ingress
The Ranger operator exposes its ports using the [Traefik](https://charmhub.io/traefik-k8s) ingress charm or any charm that implements the `ingress` interface (such as [gateway-api-integrator](https://charmhub.io/gateway-api-integrator)).

Deploy and integrate Traefik in subdomain routing mode:

```
juju deploy traefik-k8s --config routing_mode=subdomain --config external_hostname=<your-domain>
juju integrate ranger-k8s traefik-k8s
```

Once integrated, the charm automatically resolves the `policy-mgr-url` advertised to policy clients (e.g. Trino) using this priority:

1. The `policy-mgr-url` config option, if set — takes precedence over everything else.
2. The live ingress URL provided by the `ingress` relation.
3. The cluster-internal DNS: `http://ranger-k8s.<model>.svc.cluster.local:6080`.

For in-cluster policy clients that don't need to traverse the ingress hop, set the override explicitly:

```
juju config ranger-k8s policy-mgr-url=http://ranger-k8s.<model>.svc.cluster.local:6080
```

## Backup and restore
### Setting up storage
Apache Ranger is a stateless application, all of the metadata is stored in the PostgreSQL relation. Therefore backup and restore is achieved through backup and restoration of this data. A requirement for this is an [AWS S3 bucket](https://aws.amazon.com/s3/) for use with the [S3 integrator charm](https://charmhub.io/s3-integrator).

```
# Deploy the s3-integrator charm
juju deploy s3-integrator
# Provide S3 credentials
juju run s3-integrator/leader sync-s3-credentials access-key=<your_key> secret-key=<your_secret_key>
# Configure the s3-integrator
juju config s3-integrator \
    endpoint="https://s3.eu-west-2.amazonaws.com" \
    bucket="ranger-backup-bucket-1" \
    path="/ranger-backup" \
    region="eu-west-2"
# Relate postgres
juju relate s3-integratior postgresql-k8s
```

More details and configuration values can be found in the [documentation for the PostgreSQL K8s charm](https://charmhub.io/postgresql-k8s/docs/h-configure-s3-aws)

### Create and list backups
```
# Create a backup
juju run postgresql-k8s/leader create-backup --wait 5m
# List backups
juju run postgresql-k8s/leader list-backups
```
More details found [here](https://charmhub.io/postgresql-k8s/docs/h-create-and-list-backups).

### Restore a backup
```
# Check available backups
juju run postgresql-k8s/leader list-backups
# Restore backup by ID
juju run postgresql-k8s/leader restore backup-id=YYYY-MM-DDTHH:MM:SSZ --wait 5m
```
More details found [here](https://charmhub.io/postgresql-k8s/docs/h-restore-backup).

### Observability
The Apache Ranger charm can be related to the
[Canonical Observability Stack](https://charmhub.io/topics/canonical-observability-stack)
in order to collect logs and telemetry.
To deploy cos-lite and expose its endpoints as offers, follow these steps:

```bash
# Deploy the cos-lite bundle:
juju add-model cos
juju deploy cos-lite --trust
```

```bash
# Expose the cos integration endpoints:
juju offer prometheus:metrics-endpoint
juju offer loki:logging
juju offer grafana:grafana-dashboard

# Relate ranger to the cos-lite apps:
juju relate ranger-k8s admin/cos.grafana
juju relate ranger-k8s admin/cos.loki
juju relate ranger-k8s admin/cos.prometheus
```

```bash
# Access grafana with username "admin" and password:
juju run grafana/0 -m cos get-admin-password --wait 1m
# Grafana is listening on port 3000 of the app ip address.
# Dashboard can be accessed under "Ranger Admin Metrics".
```

## Contributing

This charm is still in active development. Please see the
[Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this
charm following best practice guidelines, and
[CONTRIBUTING.md](./CONTRIBUTING.md) for developer guidance.