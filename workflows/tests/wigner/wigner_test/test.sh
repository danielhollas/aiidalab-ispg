#!/bin/bash
set -e
rm -f initconds_new.xyz initconds.xyz
# Create reference data
../wigner_sharc.py -f 1 -x -n 2 freq_single.molden > /dev/null

# TODO: This currently fails
echo "Testing wigner_test.py"
../tst_wigner.py > /dev/null
diff initconds.xyz initconds_new.xyz
