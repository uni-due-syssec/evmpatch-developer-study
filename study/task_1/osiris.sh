#!/bin/bash
DOCKER_IMAGE=osiris

osiris() {
    echo "Running OSIRIS on $1/$2"
    pushd $1
    cmd="podman"
    echo "osiris: $3 -s $2" | tee -a osiris.log
    $cmd run --rm -it -v $PWD:/data:Z \
        $DOCKER_IMAGE \
        $3 -s "/data/$2" 2>&1 | tee -a osiris.log
    printf "\n#####################################\n\n" >> osiris.log
    popd
}

#echo "deleting existing osiris.log files!"
find . -name "osiris.log" -delete

# we're running with different symexec parameters:

osiris ./contract_1/ batchOverflow_BecToken.sol "-t 10"
osiris ./contract_1/ batchOverflow_BecToken.sol "-glt 120"

osiris ./contract_2/ burnOverflow_Hexagon.sol "-t 10"
osiris ./contract_2/ burnOverflow_Hexagon.sol "-glt 120"

osiris ./contract_3/ multiOverflow_Token.sol "-t 10"
osiris ./contract_3/ multiOverflow_Token.sol "-glt 120"
