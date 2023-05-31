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
tox run -e format        # update your code according to linting rules
tox run -e lint          # code style
tox run -e unit          # unit tests
tox run -e integration   # integration tests
tox                      # runs 'format', 'lint', and 'unit' environments
```
## Building Ranger image 

Given that an official Ranger image is not available yet, you are required to build it manually.
To do so, clone the following repository in a temporary location and build image using docker build:
```bash
git clone https://github.com/canonical/kafka-ranger-poc.git
cd kafka-ranger-poc/ranger
# note that the prefix localhost:32000 is there so that we can push it to the microk8s registry
docker build -t localhost:32000/ranger:2.4.0 .

# push it to the microk8s registry
docker push localhost:32000/ranger:2.4.0
```

## Build the charm

Build the charm in this git repository using:

```shell
charmcraft pack
```
The charm file `ranger-k8s_ubuntu-20.04-amd64.charm` would be created in the root folder.


<!-- You may want to include any contribution/style guidelines in this document>
