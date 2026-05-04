# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

A lot of the commands you would need are covered with the [Makefile](./Makefile), learn more by running `make help`.

## Environment for coding

You can create an environment for development with:

```shell
make venv
source venv/bin/activate
```

## Environment for building

You can install the dependencies for building with:

```shell
# LXD
sudo snap install lxd --classic
lxd init --auto

# Charmcraft
sudo snap install charmcraft --classic

# Rockcraft
sudo snap install rockcraft --classic

# yq
sudo snap install yq

# Required by import_rock.sh
sudo snap alias rockcraft.skopeo skopeo
```

### Verify build environment

```shell
make check-build-deps
```

## Environment for deploying

```shell
# MicroK8s
sudo snap install microk8s --channel 1.31-strict/stable

sudo microk8s enable hostpath-storage dns registry

sudo usermod -aG snap_microk8s $USER
newgrp snap_microk8s

# Juju
sudo snap install juju --channel 3/stable

juju bootstrap microk8s ranger-controller
juju add-model ranger-k8s

# Enable DEBUG logging:
juju model-config logging-config="<root>=INFO;unit=DEBUG"

# Docker
sudo snap install docker
sudo groupadd docker
sudo usermod -aG docker $USER
newgrp docker
```

### Verify deployment environment

```shell
make check-deploy-deps
```

## Building artifacts

You can build the charm with:

```shell
make build-charm
```

You can build the rock with:

```shell
make build-rock
```

**Note:** The rock build target watches for changes to `rockcraft.yaml` for deciding
if it needs to rebuild. If you need to change something else, e.g. scripts that go
into the rock, `touch ranger_rock/rockcraft.yaml` to force a build.

## Code quality

```shell
make fmt     # Runs formatters
make lint    # Runs linters
make test    # Runs static analysis and unit tests
make checks  # Runs all of the above

make test-integration  # Runs integration tests*
```

\*: It is recommended to let CI runners run integration tests on GitHub Actions.

## Deploying locally

First, build and import the rock into MicroK8s (run on the host where you built):

```shell
make import-rock
```

Then, deploy with the local resource:

```shell
make deploy-local
```

Relate it to dependencies:

```shell
juju deploy postgresql-k8s --channel 14/stable --trust
juju relate ranger-k8s postgresql-k8s:database
```

Refer to [README.md](./README.md) for more deployment details including
OpenSearch integration, ingress, user sync, and observability setup.
