# Charmed Apache Ranger ROCK

This repository contains the packaging metadata for creating a Charmed Ranger ROCK. This ROCK image is based on the upstream [Apache Ranger](https://downloads.apache.org/ranger/) image.

For more information on ROCKs, visit the [rockcraft Github](https://github.com/canonical/rockcraft).

## Building the ROCK
The steps outlined below are based on the assumption that you are building the ROCK with Ubuntu 22.04.
If you are using another version of Ubuntu or another operating system, the process may be different. 
To avoid any issue with other operating systems you can simply build the image with [multipass](https://multipass.run/):
```bash
sudo snap install multipass
multipass launch -n rock-dev -m 8g -c 2 -d 20G
multipass shell rock-dev
```
### Clone Repository
```bash
git clone https://github.com/canonical/ranger-k8s-operator.git
cd ranger_rock
```
### Installing & Configuring Prerequisites
```bash
sudo snap install rockcraft --edge --classic
sudo snap install skopeo --edge --devmode
sudo lxd init --auto

# Note: Docker must be installed after LXD is initialized due to firewall rules incompatibility.
sudo snap install docker
sudo groupadd docker
sudo usermod -aG docker $USER
newgrp docker

# Note: disabling and enabling docker snap is required to avoid sudo requirement. 
# As described in https://github.com/docker-snap/docker-snap.
sudo snap disable docker
sudo snap enable docker
```
### Packing and Running the ROCK
```bash
rockcraft pack
sudo skopeo --insecure-policy copy oci-archive:charmed-ranger-rock_2.4.0-22.04-edge_amd64.rock docker-daemon:charmed-ranger-rock:2.4.0
docker run -d --name ranger-ui-services -p 6080:6080 charmed-ranger-rock:2.4.0 --args ranger-admin -g 'daemon off:' \; start ranger-admin
```
### Login
To access Ranger, now exit the multipass instance run `multipass list` for the below output.
```
Name                    State             IPv4             Image
rock-dev                Running           10.137.215.60    Ubuntu 22.04 LTS
                                          10.194.234.1
                                          172.17.0.1
```
Navigate to `<VM IP Address>:6080` (in this case 10.137.215.60:6080) and login with `admin`/`rangerR0cks!`. 
Further information on connecting services and creating policies can be found at [ranger.apache.org](https://ranger.apache.org/blogs/policy_model.html).

## Patching Ranger v2.8.0

The build applies `patches/001-jdk21-compatibility.patch` to the upstream
[release-ranger-2.8.0](https://github.com/apache/ranger/tree/release-ranger-2.8.0) source before compilation. This patch is required because
upstream Ranger 2.8.0 targets JDK 8 while the rock builds with JDK 21.

The patch makes the following changes:

1. **Nashorn engine replacement:** The JDK-bundled Nashorn scripting engine
   (`jdk.nashorn.*`) was removed in JDK 15. The patch switches the imports in
   `NashornScriptEngineCreator` and `RecordFilterJavaScript` to the standalone
   `org.openjdk.nashorn:nashorn-core:15.4` library and adds it as a dependency
   in `agents-common/pom.xml`.

2. **JAXB dependency fixes:** Adds explicit `jaxb-core` and `jaxb-impl`
   dependency management entries and corrects the `jaxb-runtime` version
   property to prevent classpath conflicts under JDK 21.

3. **Enunciate upgrade:** The bundled Enunciate version 2.13.2 does not
   support JDK 21. The patch bumps it to 2.17.1.

4. **Elasticsearch audit protocol:** Changes the default
   `ranger.audit.elasticsearch.protocol` from `http` to `https` in
   `ranger-admin-site.xml`.

## Using Makefile

`make dev` will create the multipass image, clone the repo, install and configure the prerequisites.
`make build` will pack the rock for you
`make install` will build the oci image and run it
`make clean` will remove build artifacts and enable you to rebuild
`make clean-all` will remove the image and get you ready to start from `make dev`

## License
The Charmed Ranger ROCK is free software, distributed under the Apache
Software License, version 2.0. See
[LICENSE](https://github.com/canonical/charmed-ranger-rock/blob/main/LICENSE)
for more information.