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

### Building Ranger image 

Given that an official Ranger image is not available yet, you are required to build it manually.
To do so, clone the following repository in a temporary location and build image using docker build:
```bash
git clone https://github.com/canonical/kafka-ranger-poc.git
cd kafka-ranger-poc/ranger
# note that the prefix localhost:3200 is there so that we can push it to the microk8s registry
docker build -t localhost:3200/ranger:2.4.0 .

# push it to the microk8s registry
docker push localhost:3200/ranger:2.4.0
```
### Building the charm
To build the charm simply run:
```bash 
charmcraft pack
```
The charm file `ranger-k8s_ubuntu-20.04-amd64.charm` would be created in the root folder.

### Deploying Ranger and PostgreSQL Database in MicroK8S

Ranger requires PostgreSQL to store its state. 
Therefore, its deployment requires a relation with the Postgres charm:

```bash
# this will be blocked until the relation with Postgres is created 
juju deploy ./ranger-k8s_ubuntu-20.04-amd64.charm --resource ranger-image=localhost:32000/ranger:2.4.0
juju deploy postgresql-k8s --channel 14/stable --trust
juju relate ranger-k8s:db postgresql-k8s:database
```

## Contributing

This charm is still in active development. Please see the
[Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this
charm following best practice guidelines, and
[CONTRIBUTING.md](./CONTRIBUTING.md) for developer guidance.