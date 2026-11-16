"""Public configuration metadata for the exact retained qualification runtime.

This synthetic source fixture contains no image payload and grants no runtime
authority. The configuration identity is preserved to exercise the production
validator rather than patch its immutable image constants.
"""
from __future__ import annotations
import json
from typing import Any

_PUBLIC_METADATA = r'''{
  "base_config": "sha256:2e7ed4ea03deedbb45c26c68bf2a7090dc68dd8518a9c75928c4b9b0f59bc411",
  "runtime_config": "sha256:4b8c5fb11ee4bd545a4cc7db662932d74c8213e0016bbb92d3d6b5809476a1f8",
  "runtime_config_document": {
    "architecture": "arm64",
    "config": {
      "Cmd": [
        "postgres"
      ],
      "Entrypoint": [
        "docker-entrypoint.sh"
      ],
      "Env": [
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/postgresql/16/bin",
        "GOSU_VERSION=1.19",
        "LANG=en_US.utf8",
        "PG_MAJOR=16",
        "PG_VERSION=16.16-1.pgdg13+1",
        "PGDATA=/var/lib/postgresql/data"
      ],
      "ExposedPorts": {
        "5432/tcp": {}
      },
      "Labels": {
        "org.repomap.test.pgvector.owner": "CLOE-W5-PGDG1",
        "org.repomap.test.pgvector.package_sha256": "fa7c26fe2f076538915fa7efac6cd944777d891ccb251324bea01fda35626919",
        "org.repomap.test.pgvector.recipe": "10e6ab0e09ab000b0759b51d8893d8d8d2ccb47cb61f2a29828674dbf4b2b191",
        "org.repomap.test.profile": "pgvector"
      },
      "StopSignal": "SIGINT",
      "Volumes": {
        "/var/lib/postgresql/data": {}
      }
    },
    "created": "1970-01-01T00:00:00Z",
    "history": [
      {
        "comment": "debuerreotype 0.17",
        "created": "2026-11-04T21:23:39Z",
        "created_by": "# debian.sh --arch 'arm64' out/ 'trixie' '@1793827419'"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:53:45.876236793Z",
        "created_by": "RUN /bin/sh -c set -eux; \tgroupadd -r postgres --gid=999; \tuseradd -r -g postgres --uid=999 --home-dir=/var/lib/postgresql --shell=/bin/bash postgres; \tinstall --verbose --directory --owner postgres --group postgres --mode 1777 /var/lib/postgresql # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:53:52.27285584Z",
        "created_by": "RUN /bin/sh -c set -ex; \tapt-get update; \tapt-get install -y --no-install-recommends \t\tgnupg \t\tless \t; \trm -rf /var/lib/apt/lists/* # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:53:59.69217929Z",
        "created_by": "ENV GOSU_VERSION=1.19",
        "empty_layer": true
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:53:59.69217929Z",
        "created_by": "RUN /bin/sh -c set -eux; \tsavedAptMark=\"$(apt-mark showmanual)\"; \tapt-get update; \tapt-get install -y --no-install-recommends ca-certificates wget; \trm -rf /var/lib/apt/lists/*; \tdpkgArch=\"$(dpkg --print-architecture | awk -F- '{ print $NF }')\"; \twget -O /usr/local/bin/gosu \"https://github.com/tianon/gosu/releases/download/$GOSU_VERSION/gosu-$dpkgArch\"; \twget -O /usr/local/bin/gosu.asc \"https://github.com/tianon/gosu/releases/download/$GOSU_VERSION/gosu-$dpkgArch.asc\"; \texport GNUPGHOME=\"$(mktemp -d)\"; \tgpg --batch --keyserver hkps://keys.openpgp.org --recv-keys B42F6819007F00F88E364FD4036A9C25BF357DD4; \tgpg --batch --verify /usr/local/bin/gosu.asc /usr/local/bin/gosu; \tgpgconf --kill all; \trm -rf \"$GNUPGHOME\" /usr/local/bin/gosu.asc; \tapt-mark auto '.*' > /dev/null; \t[ -z \"$savedAptMark\" ] || apt-mark manual $savedAptMark > /dev/null; \tapt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false; \tchmod +x /usr/local/bin/gosu; \tgosu --version; \tgosu nobody true # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:54:05.336903408Z",
        "created_by": "RUN /bin/sh -c set -eux; \tif [ -f /etc/dpkg/dpkg.cfg.d/docker ]; then \t\tgrep -q '/usr/share/locale' /etc/dpkg/dpkg.cfg.d/docker; \t\tsed -ri '/\\/usr\\/share\\/locale/d' /etc/dpkg/dpkg.cfg.d/docker; \t\t! grep -q '/usr/share/locale' /etc/dpkg/dpkg.cfg.d/docker; \tfi; \tapt-get update; apt-get install -y --no-install-recommends locales; rm -rf /var/lib/apt/lists/*; \techo 'en_US.UTF-8 UTF-8' >> /etc/locale.gen; \tlocale-gen; \tlocale -a | grep 'en_US.utf8' # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:54:05.336903408Z",
        "created_by": "ENV LANG=en_US.utf8",
        "empty_layer": true
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:54:09.139129499Z",
        "created_by": "RUN /bin/sh -c set -eux; \tapt-get update; \tapt-get install -y --no-install-recommends \t\tlibnss-wrapper \t\txz-utils \t\tzstd \t; \trm -rf /var/lib/apt/lists/* # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:54:09.253665155Z",
        "created_by": "RUN /bin/sh -c mkdir /docker-entrypoint-initdb.d # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:54:09.784384034Z",
        "created_by": "RUN /bin/sh -c set -ex; \tkey='B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8'; \texport GNUPGHOME=\"$(mktemp -d)\"; \tmkdir -p /usr/local/share/keyrings/; \tgpg --batch --keyserver keyserver.ubuntu.com --recv-keys \"$key\"; \tgpg --batch --export --armor \"$key\" > /usr/local/share/keyrings/postgres.gpg.asc; \tgpgconf --kill all; \trm -rf \"$GNUPGHOME\" # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:54:09.784384034Z",
        "created_by": "ENV PG_MAJOR=16",
        "empty_layer": true
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:54:09.784384034Z",
        "created_by": "ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/postgresql/16/bin",
        "empty_layer": true
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:54:09.784384034Z",
        "created_by": "ENV PG_VERSION=16.16-1.pgdg13+1",
        "empty_layer": true
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.312959307Z",
        "created_by": "RUN /bin/sh -c set -ex; \t\texport PYTHONDONTWRITEBYTECODE=1; \t\tdpkgArch=\"$(dpkg --print-architecture)\"; \taptRepo=\"[ signed-by=/usr/local/share/keyrings/postgres.gpg.asc ] http://apt.postgresql.org/pub/repos/apt trixie-pgdg main $PG_MAJOR\"; \tcase \"$dpkgArch\" in \t\tamd64 | arm64 | loong64 | ppc64el) \t\t\techo \"deb $aptRepo\" > /etc/apt/sources.list.d/pgdg.list; \t\t\tapt-get update; \t\t\t;; \t\t*) \t\t\techo \"deb-src $aptRepo\" > /etc/apt/sources.list.d/pgdg.list; \t\t\t\t\t\tsavedAptMark=\"$(apt-mark showmanual)\"; \t\t\t\t\t\ttempDir=\"$(mktemp -d)\"; \t\t\tcd \"$tempDir\"; \t\t\t\t\t\tapt-get update; \t\t\tapt-get install -y --no-install-recommends dpkg-dev; \t\t\techo \"deb [ trusted=yes ] file://$tempDir ./\" > /etc/apt/sources.list.d/temp.list; \t\t\t_update_repo() { \t\t\t\tdpkg-scanpackages . > Packages; \t\t\t\tapt-get -o Acquire::GzipIndexes=false update; \t\t\t}; \t\t\t_update_repo; \t\t\t\t\t\tnproc=\"$(nproc)\"; \t\t\texport DEB_BUILD_OPTIONS=\"nocheck parallel=$nproc\"; \t\t\tapt-get build-dep -y postgresql-common-dev; \t\t\tapt-get source --compile postgresql-common-dev; \t\t\t_update_repo; \t\t\tapt-get build-dep -y \"postgresql-$PG_MAJOR=$PG_VERSION\"; \t\t\tapt-get source --compile \"postgresql-$PG_MAJOR=$PG_VERSION\"; \t\t\t\t\t\t\t\t\tapt-mark showmanual | xargs apt-mark auto > /dev/null; \t\t\tapt-mark manual $savedAptMark; \t\t\t\t\t\tls -lAFh; \t\t\t_update_repo; \t\t\tgrep '^Package: ' Packages; \t\t\tcd /; \t\t\t;; \tesac; \t\tapt-get install -y --no-install-recommends postgresql-common; \tsed -ri 's/#(create_main_cluster) .*$/\\1 = false/' /etc/postgresql-common/createcluster.conf; \tapt-get install -y --no-install-recommends \t\t\"postgresql-$PG_MAJOR=$PG_VERSION\" \t; \t\trm -rf /var/lib/apt/lists/*; \t\tif [ -n \"$tempDir\" ]; then \t\tapt-get purge -y --auto-remove; \t\trm -rf \"$tempDir\" /etc/apt/sources.list.d/temp.list; \tfi; \t\tfind /usr -name '*.pyc' -type f -exec bash -c 'for pyc; do dpkg -S \"$pyc\" &> /dev/null || rm -vf \"$pyc\"; done' -- '{}' +; \t\tpostgres --version # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.422350187Z",
        "created_by": "RUN /bin/sh -c set -eux; \tdpkg-divert --add --rename --divert \"/usr/share/postgresql/postgresql.conf.sample.dpkg\" \"/usr/share/postgresql/$PG_MAJOR/postgresql.conf.sample\"; \tcp -v /usr/share/postgresql/postgresql.conf.sample.dpkg /usr/share/postgresql/postgresql.conf.sample; \tln -sv ../postgresql.conf.sample \"/usr/share/postgresql/$PG_MAJOR/\"; \tsed -ri \"s!^#?(listen_addresses)\\s*=\\s*\\S+.*!\\1 = '*'!\" /usr/share/postgresql/postgresql.conf.sample; \tgrep -F \"listen_addresses = '*'\" /usr/share/postgresql/postgresql.conf.sample # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.497346629Z",
        "created_by": "RUN /bin/sh -c install --verbose --directory --owner postgres --group postgres --mode 3777 /var/run/postgresql # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.497346629Z",
        "created_by": "ENV PGDATA=/var/lib/postgresql/data",
        "empty_layer": true
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.589590241Z",
        "created_by": "RUN /bin/sh -c install --verbose --directory --owner postgres --group postgres --mode 1777 \"$PGDATA\" # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.589590241Z",
        "created_by": "VOLUME [/var/lib/postgresql/data]",
        "empty_layer": true
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.611147462Z",
        "created_by": "COPY docker-entrypoint.sh docker-ensure-initdb.sh /usr/local/bin/ # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.677559288Z",
        "created_by": "RUN /bin/sh -c ln -sT docker-ensure-initdb.sh /usr/local/bin/docker-enforce-initdb.sh # buildkit"
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.677559288Z",
        "created_by": "ENTRYPOINT [\"docker-entrypoint.sh\"]",
        "empty_layer": true
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.677559288Z",
        "created_by": "STOPSIGNAL SIGINT",
        "empty_layer": true
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.677559288Z",
        "created_by": "EXPOSE map[5432/tcp:{}]",
        "empty_layer": true
      },
      {
        "comment": "buildkit.dockerfile.v0",
        "created": "2026-11-12T20:55:16.677559288Z",
        "created_by": "CMD [\"postgres\"]",
        "empty_layer": true
      },
      {
        "created": "1970-01-01T00:00:00Z",
        "created_by": "common-lore-index offline selected PGDG provider"
      }
    ],
    "os": "linux",
    "rootfs": {
      "diff_ids": [
        "sha256:04e61ff9ed2c84e88795bf7562d69d0980188e2bb6d6152d6fe721383e10db9e",
        "sha256:27e31ada13f2c66a7388a094a62acafd6a120738b19f11309ad0e138c0433052",
        "sha256:4866e0b4c5b738eb0551a4b718a64dad9676940e8626fb81ccd5826a1b8e9325",
        "sha256:4144e925c66e734ba9841e614fec685fe99c21906a2ae36db1443298e5e71f1d",
        "sha256:e1fa6a350a540edbecec8140fc023507f4ee103ec93e0897b81dfb65b3d088b7",
        "sha256:53a3286493f7cac4933ebf9ba17afb47ba1fb3efb84d907a61090330f5b413da",
        "sha256:04842cc792828051ad334e2381cc7764c42d7ad6b64533faca9cfd6273a8769e",
        "sha256:13f38c630b4282ace858daf0465b8d6e0bcea9324cf1361827062d4c7b02650a",
        "sha256:3e50b85a1afbac48453ecaf96f1a4555f22f232c08c6ed1e14976c6ffea50856",
        "sha256:73077e19e094d3447035b2458fe73aa566b86b04cf2f7a7406137a5360f514c4",
        "sha256:0e4abda6e9c17b592ff85900ded20ca1c959b10f5b902b2cb412e790e6b8f162",
        "sha256:5c0288d43de0c3eac5d22c6b91c99c5b2d26f4aab21b42c0a400192455fcb8e0",
        "sha256:114eea4fbb973b4da3352b58debad3628ac60f261b76b6e0efc17b5f04d6fdc2",
        "sha256:10b2c58e9938bba99546422c14193996e1ab8da7a3ca6f3e8227ff952be3df39",
        "sha256:ecfc2dd520cd1d440ed3a070c6a3a116075477d6fc916c44a751fb948bf58bef"
      ],
      "type": "layers"
    },
    "variant": "v8"
  },
  "runtime_layers": [
    {
      "bytes": 103178240,
      "name": "layers/04e61ff9ed2c84e88795bf7562d69d0980188e2bb6d6152d6fe721383e10db9e.tar",
      "sha256": "04e61ff9ed2c84e88795bf7562d69d0980188e2bb6d6152d6fe721383e10db9e"
    },
    {
      "bytes": 12288,
      "name": "layers/27e31ada13f2c66a7388a094a62acafd6a120738b19f11309ad0e138c0433052.tar",
      "sha256": "27e31ada13f2c66a7388a094a62acafd6a120738b19f11309ad0e138c0433052"
    },
    {
      "bytes": 17092096,
      "name": "layers/4866e0b4c5b738eb0551a4b718a64dad9676940e8626fb81ccd5826a1b8e9325.tar",
      "sha256": "4866e0b4c5b738eb0551a4b718a64dad9676940e8626fb81ccd5826a1b8e9325"
    },
    {
      "bytes": 3713024,
      "name": "layers/4144e925c66e734ba9841e614fec685fe99c21906a2ae36db1443298e5e71f1d.tar",
      "sha256": "4144e925c66e734ba9841e614fec685fe99c21906a2ae36db1443298e5e71f1d"
    },
    {
      "bytes": 26182144,
      "name": "layers/e1fa6a350a540edbecec8140fc023507f4ee103ec93e0897b81dfb65b3d088b7.tar",
      "sha256": "e1fa6a350a540edbecec8140fc023507f4ee103ec93e0897b81dfb65b3d088b7"
    },
    {
      "bytes": 3414528,
      "name": "layers/53a3286493f7cac4933ebf9ba17afb47ba1fb3efb84d907a61090330f5b413da.tar",
      "sha256": "53a3286493f7cac4933ebf9ba17afb47ba1fb3efb84d907a61090330f5b413da"
    },
    {
      "bytes": 1536,
      "name": "layers/04842cc792828051ad334e2381cc7764c42d7ad6b64533faca9cfd6273a8769e.tar",
      "sha256": "04842cc792828051ad334e2381cc7764c42d7ad6b64533faca9cfd6273a8769e"
    },
    {
      "bytes": 7680,
      "name": "layers/13f38c630b4282ace858daf0465b8d6e0bcea9324cf1361827062d4c7b02650a.tar",
      "sha256": "13f38c630b4282ace858daf0465b8d6e0bcea9324cf1361827062d4c7b02650a"
    },
    {
      "bytes": 348493824,
      "name": "layers/3e50b85a1afbac48453ecaf96f1a4555f22f232c08c6ed1e14976c6ffea50856.tar",
      "sha256": "3e50b85a1afbac48453ecaf96f1a4555f22f232c08c6ed1e14976c6ffea50856"
    },
    {
      "bytes": 69632,
      "name": "layers/73077e19e094d3447035b2458fe73aa566b86b04cf2f7a7406137a5360f514c4.tar",
      "sha256": "73077e19e094d3447035b2458fe73aa566b86b04cf2f7a7406137a5360f514c4"
    },
    {
      "bytes": 2048,
      "name": "layers/0e4abda6e9c17b592ff85900ded20ca1c959b10f5b902b2cb412e790e6b8f162.tar",
      "sha256": "0e4abda6e9c17b592ff85900ded20ca1c959b10f5b902b2cb412e790e6b8f162"
    },
    {
      "bytes": 3072,
      "name": "layers/5c0288d43de0c3eac5d22c6b91c99c5b2d26f4aab21b42c0a400192455fcb8e0.tar",
      "sha256": "5c0288d43de0c3eac5d22c6b91c99c5b2d26f4aab21b42c0a400192455fcb8e0"
    },
    {
      "bytes": 20992,
      "name": "layers/114eea4fbb973b4da3352b58debad3628ac60f261b76b6e0efc17b5f04d6fdc2.tar",
      "sha256": "114eea4fbb973b4da3352b58debad3628ac60f261b76b6e0efc17b5f04d6fdc2"
    },
    {
      "bytes": 3072,
      "name": "layers/10b2c58e9938bba99546422c14193996e1ab8da7a3ca6f3e8227ff952be3df39.tar",
      "sha256": "10b2c58e9938bba99546422c14193996e1ab8da7a3ca6f3e8227ff952be3df39"
    },
    {
      "bytes": 327680,
      "name": "layers/ecfc2dd520cd1d440ed3a070c6a3a116075477d6fc916c44a751fb948bf58bef.tar",
      "sha256": "ecfc2dd520cd1d440ed3a070c6a3a116075477d6fc916c44a751fb948bf58bef"
    }
  ]
}'''

def runtime_provenance_fixture() -> dict[str, Any]:
    return dict(json.loads(_PUBLIC_METADATA))
