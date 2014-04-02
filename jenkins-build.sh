#!/bin/sh
#
# this is the script run by the Jenkins server to run the build and tests.  Be
# sure to always run it in its dir, i.e. ./jenkins-build.sh, otherwise it might
# remove things that you don't want it to.

if [ `dirname $0` != "." ]; then
    echo "only run this script like ./`basename $0`"
    exit
fi

set -e
set -x

if [ -z $WORKSPACE ]; then
    export WORKSPACE=`pwd`
fi

if [ -z $ANDROID_HOME ]; then
    if [ -e ~/.android/bashrc ]; then
        . ~/.android/bashrc
    else
        echo "ANDROID_HOME must be set!"
        exit
    fi
fi

#------------------------------------------------------------------------------#
# required Java 7 keytool/jarsigner for :file support

export PATH=/usr/lib/jvm/java-7-openjdk-amd64/bin:$PATH

#------------------------------------------------------------------------------#
# run local build
cd $WORKSPACE/fdroidserver/getsig
./make.sh


#------------------------------------------------------------------------------#
# run local tests
cd $WORKSPACE/tests
./run-tests.sh


#------------------------------------------------------------------------------#
# test building the source tarball
cd $WORKSPACE
python setup.py sdist


#------------------------------------------------------------------------------#
# test install using site packages
cd $WORKSPACE
rm -rf $WORKSPACE/env
virtualenv --system-site-packages $WORKSPACE/env
. $WORKSPACE/env/bin/activate
pip install -e $WORKSPACE
python setup.py install

# run tests in new pip+virtualenv install
. $WORKSPACE/env/bin/activate
fdroid=$WORKSPACE/env/bin/fdroid $WORKSPACE/tests/run-tests.sh


#------------------------------------------------------------------------------#
# run pyflakes
pyflakes fdroid makebuildserver fdroidserver/*.py setup.py


#------------------------------------------------------------------------------#
# run pylint

cd $WORKSPACE
set +e
# disable E1101 until there is a plugin to handle this properly:
#   Module 'sys' has no '_MEIPASS' member
# disable F0401 until there is a plugin to handle this properly:
#   keysync-gui:25: [F] Unable to import 'ordereddict'
pylint --output-format=parseable --reports=n \
    fdroidserver/*.py fdroid makebuildserver setup.py > $WORKSPACE/pylint.parseable

# to only tell jenkins there was an error if we got ERROR or FATAL, uncomment these:
#[ $(($? & 1)) = "1" ] && exit 1
#[ $(($? & 2)) = "2" ] && exit 2
set -e

