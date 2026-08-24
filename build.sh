#!/bin/sh
#
# Builds the deployable for the *processor* app in this repo
# (`ubiquiti_network_overview`): a package.zip of vendored dependencies plus the
# source tree, and the React widget bundle the app's UI schema points at.
#
# The AirMax app is not built here — it is a device app and ships as a Docker
# image. `doover app discover` tells the two apart and CI runs the right job for
# each, so this script only ever concerns the processor.
set -e

rm -rf packages_export requirements.txt

# --no-editable so the vendored tree is real files rather than a path link back
# to this checkout, which would not survive being zipped.
uv export --frozen --no-dev --no-editable --quiet -o requirements.txt

uv pip install \
   --no-deps \
   --no-installer-metadata \
   --no-compile-bytecode \
   --python-platform x86_64-manylinux2014 \
   --python 3.13 \
   --quiet \
   --target packages_export \
   --refresh \
   -r requirements.txt

rm -f package.zip
mkdir -p packages_export

cd packages_export
zip -rq ../package.zip .
cd ..

# NOTE: the source tree is NOT zipped in separately. `uv export` above emits the
# project itself as a non-editable dependency, so `uv pip install` already
# vendors `ubiquiti_network_overview/` into packages_export as a real package —
# which is why the handler is `ubiquiti_network_overview.handler` and not
# `src.…`. Adding `zip package.zip src` on top puts a second copy of the same
# modules in the archive, and which one imports then depends on sys.path order.

# The remote component the app's UI schema loads. Produces
# widget/assets/UbiquitiNetworkWidget.js, which the app block references as its
# `widget`.
if [ -d widget ]; then
  npm --prefix widget install
  npm --prefix widget run build
fi

echo "OK"
