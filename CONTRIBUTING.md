# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

A `Makefile` is provided at the root of the repository to simplify common development tasks. Run `make help` to see all available targets.

**Note:** It is recommended to build on the host and deploy in a Multipass instance. Launch a VM and mount the project directory so the VM has access to build artifacts:
```shell
multipass launch 24.04 -n ranger-dev -m 8g -c 2 -d 40G
multipass mount ~/path/to/ranger-k8s-operator ranger-dev:/home/ubuntu/ranger-k8s-operator
multipass shell ranger-dev
```

## Set up a virtual environment

```shell
make venv
source venv/bin/activate
```

## Install dependencies

Install all dependencies needed to build and test the charm:

```shell
make install-build-deps
```

Install dependencies needed to deploy locally (Juju, MicroK8s, Docker):

```shell
make install-deploy-deps
```

You can verify dependencies are available with:

```shell
make check-build-deps
make check-deploy-deps
```

## Testing

This project uses `tox` for managing test environments. Run checks via `make` or `tox` directly:

```shell
make fmt              # apply coding style standards
make lint             # check code against coding style standards
make test-unit        # run unit tests
make test-static      # run static type checks
make test             # run unit and static tests
make test-integration # run integration tests
make checks           # run fmt, lint, and test
```

## Set up your development environment

### Install MicroK8s

```shell
# Install MicroK8s from snap:
sudo snap install microk8s --channel 1.34-strict/stable

# Add your user to the MicroK8s group:
sudo usermod -a -G snap_microk8s $USER

# Switch to microk8s group:
newgrp snap_microk8s

# Create the ~/.kube/ directory and load microk8s configuration:
mkdir -p ~/.kube/ && microk8s config > ~/.kube/config

# Enable the necessary MicroK8s addons:
sudo microk8s enable hostpath-storage dns registry

# Set up a short alias for Kubernetes CLI:
sudo snap alias microk8s.kubectl kubectl
```

### Install LXD and Charmcraft

```shell
# Install LXD from snap:
sudo snap install lxd --classic --channel=5.12/stable

# Configure LXD:
lxd init --auto

# Install charmcraft from snap:
sudo snap install charmcraft --classic --channel=latest/stable
```

### Set up the Juju OLM

```shell
# Install the Juju CLI client:
sudo snap install juju --channel=3/stable

# Bootstrap a controller into your "microk8s" cloud:
juju bootstrap microk8s ranger-controller

# Create a model:
juju add-model ranger-k8s

# Enable DEBUG logging:
juju model-config logging-config="<root>=INFO;unit=DEBUG"

# Check progress:
juju status
juju debug-log
```

## Build the charm and rock

Build both the charm and the OCI image (rock):

```shell
make build
```

Or build individually:

```shell
make build-charm   # produces ranger-k8s_ubuntu-22.04-amd64.charm
make build-rock    # produces the rock OCI archive in ranger_rock/
```

## Deploy locally

Import the rock into MicroK8s and deploy the charm using local resources:

```shell
make import-rock
make deploy-local
```

## Clean up

```shell
make clean            # remove built charm and rock files
make clean-charmcraft # clean the charmcraft build environment
make clean-rockcraft  # clean the rockcraft build environment
```
