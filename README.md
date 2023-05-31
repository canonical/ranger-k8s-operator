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

## Contributing

This charm is still in active development. Please see the
[Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this
charm following best practice guidelines, and
[CONTRIBUTING.md](./CONTRIBUTING.md) for developer guidance.