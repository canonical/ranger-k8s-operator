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

```bash
# this will be blocked until the relation with Postgres is created 
juju deploy ./ranger-k8s_ubuntu-20.04-amd64.charm --resource ranger-image=localhost:32000/ranger:2.4.0
juju deploy postgresql-k8s --channel 14/stable --trust
juju relate ranger-k8s:db postgresql-k8s:database
```
Refer to [CONTRIBUTING.md](./CONTRIBUTING.md) for details on building the charm and ranger image. 

### Group management with Apache Ranger
The Charmed Ranger Operator makes use of the [Ranger API](https://ranger.apache.org/apidocs/index.html) and [apache-ranger PyPi package](https://pypi.org/project/apache-ranger/) to manage users and groups. The source of users and group memberships is a `user-group-configuration.yaml` file provided to the charm as a configuration value `user-group-configuration`. 

An example of this file is here: 
```
ranger-k8s:
   user-group-configuration: |
      trino-service:
         users:
            - name: user1
              firstname: One
              lastname: User
              email: user1@canonical.com
            - name: user2
              firstname: Two
              lastname: User
              email: user2@canonical.com

         groups:
            - name: developers
              description: users with developer level access.
            - name: users
              description: users with select only access.

         memberships:
            - groupname: users
              users: [user1, user2]
            - groupname: developers
              users: [user2]
```
The charm will automatically sync users and groups from the configuration file to Ranger admin. Removing groups and group memberships where not present in the configuration file. This means that you should not create groups and user associations directly in the UI as they will be removed if not present in the file.

#### Group management in related application
Related applications must have the Ranger plugin configured. The Ranger plugin schedules regular download of Ranger policies (every 3 minutes) storing these policies within the related application in a cache. On access request, the requesting user's UNIX group is used when comparing to Ranger group policies to determine access. 

#### Service name
Before relation of an application to the Ranger charm, the application's `ranger-service-name` configuration parameter should be set. This will be the name of the Ranger service created for the application. When creating the `user-group-configuration.yaml` this service name should be used as the key for user and group data. Users will automatically be synchronized with the related charm matching the service name.

#### Trino relation
The configuration of these groups is done automatically on relation with the Ranger charm in the [Trino K8s charm](https://charmhub.io/trino-k8s).

```
# relate trino and ranger charms:
juju relate trino-k8s:policy ranger-k8s:policy

# confirm applications are related and wait until active:
juju status --relations

# provide the ranger configuration file:
juju config ranger-k8s --file=user-group-configuration.yaml
```

### Ingress
The Ranger operator exposes its ports using the Nginx Ingress Integrator operator. You must first make sure to have an Nginx Ingress Controller deployed. To enable TLS connections, you must have a TLS certificate stored as a k8s secret (default name is "ranger-tls"). A self-signed certificate for development purposes can be created as follows:

```
# Generate private key
openssl genrsa -out server.key 2048

# Generate a certificate signing request
openssl req -new -key server.key -out server.csr -subj "/CN=ranger-k8s"

# Create self-signed certificate
openssl x509 -req -days 365 -in server.csr -signkey server.key -out server.crt -extfile <(printf "subjectAltName=DNS:ranger-k8s")

# Create a k8s secret
kubectl create secret tls ranger-tls --cert=server.crt --key=server.key
```
This operator can then be deployed and connected to the ranger operator using the Juju command line as follows:

```
# Deploy ingress controller.
microk8s enable ingress:default-ssl-certificate=ranger-k8s/ranger-tls

juju deploy nginx-ingress-integrator --channel edge --revision 71
juju relate ranger-k8s nginx-ingress-integrator
```

Once deployed, the hostname will default to the name of the application (ranger-k8s), and can be configured using the external-hostname configuration on the ranger operator.

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

## Contributing

This charm is still in active development. Please see the
[Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this
charm following best practice guidelines, and
[CONTRIBUTING.md](./CONTRIBUTING.md) for developer guidance.