#!/bin/sh
# root: a second test node (node2) against the scripted fake relay on 127.0.0.1:17460.
cd /build/lanes/asklink || exit 1
sh vm/node.sh stop node2 >/dev/null 2>&1
sh vm/node.sh prepare node2 17460 || exit 1
# the daemon runs with every malloc security option (canaries, junk, guard pages, free-unmap)
MALLOC_OPTIONS=S sh vm/node.sh start node2 || exit 1
rc=0
python3 vm/fake_test.py /build/lanes/asklink/run/node2 17460 || rc=$?
sh vm/node.sh stop node2
cp run/node2/chatd.log run/node2-chatd-fake.log
cp run/node2/fake/RESULT.json run/node2-fake-RESULT.json 2>/dev/null
sh vm/node.sh wipe node2
rm -rf run/node2/fake
exit $rc
