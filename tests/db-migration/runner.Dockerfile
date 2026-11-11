# 로컬 리허설 실행 컨테이너: mysql·mysqldump(원본과 같은 8.4), Percona Toolkit, Python(PyMySQL)
FROM mysql:8.4.11
RUN microdnf install -y perl perl-DBI perl-DBD-MySQL perl-Time-HiRes perl-IO-Socket-SSL perl-English \
      python3 python3-pip tar gzip procps-ng diffutils \
 && microdnf clean all \
 && curl -fsSL https://downloads.percona.com/downloads/percona-toolkit/3.7.0/source/tarball/percona-toolkit-3.7.0.tar.gz \
    | tar -xz -C /opt \
 && ln -s /opt/percona-toolkit-3.7.0/bin/pt-table-checksum /usr/local/bin/ \
 && pip3 install --no-cache-dir PyMySQL==1.1.1
ENTRYPOINT []
