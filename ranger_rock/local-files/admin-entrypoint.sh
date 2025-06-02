#!/bin/bash

export JVMFLAGS=-Dsun.net.spi.nameservice.provider.1=dns,sun

cd /usr/lib/ranger/admin && \
./setup.sh && \
./ews/ranger-admin-services.sh start  && \
ls -la && \
tail -f ./ews/logs/ranger-admin-ranger-*.log
