#!/usr/bin/awk
#
# AWK script for converting ORCA hessian files to molden format
#
# run as: awk -f orcahess2molden.awk orca_calc.hess
#
BEGIN {
  # This is a running total
  nmode_total=0
  row=-1
  print "[Molden Format]"
} 

$1 == "$vibrational_frequencies" {
  print "[FREQ]"
  getline
  nm_all = $1
  natom = $1 / 3
  for (i=1;i<=nm_all;i++) {
    getline
    # Ignore translations and rotations
    if (i>6) print $2
  }
}

$1 == "$atoms" {
  print "[FR-COORD]"
  getline
  for (iat=1; iat<=natom;iat++) {
    getline
    print $1, $3, $4, $5
  }
}

# Activate reading normal modes
# by setting row=0
$1 == "$normal_modes" {
  row = 0
  getline
  next
}

# Skip normal modes headers
row == 0  {
  row+=1
  atom=1
  next
}

row % 3 == 1 {
  nmode = NF - 1
  for (imode=1; imode <= nmode; imode++) {
    vibx[imode+nmode_total][atom] = $(imode+1)
  }
}

row % 3 == 2 {
  for (imode=1; imode <= nmode; imode++) {
    viby[imode+nmode_total][atom] = $(imode+1)
  }
}

row % 3 == 0 {
  for (imode=1; imode <= nmode; imode++) {
    vibz[imode+nmode_total][atom] = $(imode+1)
  }
  atom++
}

row == natom*3 {
  row = 0
  atom = 1
  nmode_total += nmode
  if (nmode_total==nm_all) row = -1
}

row > 0 {row++}

END {
  print "[FR-NORM-COORD]", nmode_total
  for (imode=7; imode <= nmode_total; imode++) {
    print "vibration", imode - 6
    for (iat=1; iat<=natom; iat++) {
      print vibx[imode][iat], viby[imode][iat], vibz[imode][iat]
    }
  }
}
