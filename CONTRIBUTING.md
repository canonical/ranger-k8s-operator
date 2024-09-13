# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

You can create an environment for development with `tox`:

```shell
tox devenv -e integration
source venv/bin/activate
```

## Testing

This project uses `tox` for managing test environments. There are some pre-configured environments
that can be used for linting and formatting code when you're preparing contributions to the charm:

```shell
tox run -e fmt        # update your code according to linting rules
tox run -e lint          # code style
tox run -e unit          # unit tests
tox run -e integration   # integration tests
tox                      # runs 'fmt', 'lint', and 'unit' environments
```

## Set up your development environment
### Install Microk8s
```
# Install Microk8s from snap:
sudo snap install microk8s --channel 1.25-strict/stable

# Add your user to the Microk8s group:
sudo usermod -a -G snap_microk8s ubuntu

# Switch to microk8s group
newgrp snap_microk8s

# Create the ~/.kube/ directory and load microk8s configuration
mkdir -p ~/.kube/ && microk8s config > ~/.kube/config

# Enable the necessary Microk8s addons:
sudo microk8s enable hostpath-storage dns

# Set up a short alias for Kubernetes CLI:
sudo snap alias microk8s.kubectl kubectl
```
### Install Charmcraft
```
# Install lxd from snap:
sudo snap install lxd --classic --channel=5.12/stable

# Install charmcraft from snap:
sudo snap install charmcraft --classic --channel=2.2/stable

# Charmcraft relies on LXD. Configure LXD:
lxd init --auto
```
### Set up the Juju OLM
```
# Install the Juju CLI client, juju:
sudo snap install juju --channel=3.1/stable

# Install a "juju" controller into your "microk8s" cloud:
juju bootstrap microk8s ranger-controller

# Create a 'model' on this controller:
juju add-model ranger-k8s

# Enable DEBUG logging:
juju model-config logging-config="<root>=INFO;unit=DEBUG"

# Check progress:
juju status
juju debug-log
```

## Build the charm

Build the charm in this git repository using:

```shell
charmcraft pack
```
The charm file `ranger-k8s_ubuntu-22.04-amd64.charm` would be created in the root folder.


<!-- You may want to include any contribution/style guidelines in this document>
