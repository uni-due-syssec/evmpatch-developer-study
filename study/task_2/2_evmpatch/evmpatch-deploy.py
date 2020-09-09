#!/usr/bin/env python

import argparse
import binascii
import json
import logging
import os
import pathlib
import subprocess as sp
import sys

import coloredlogs
import web3
from eth_utils import encode_hex

import evmpatch

# from web3.auto.gethdev import w3
log = logging.getLogger()


def _path_validator(p):
    path = pathlib.Path(p)
    # if not path.exists():
    #     log.error("No such file '%s'", path)
    #     sys.exit(-1)
    return path


# parse arguments
parser = argparse.ArgumentParser(
    description="EVMPatch Command Line Interface for Contract Deployment")
parser.add_argument("--verbose", "-v", action="store_true", default=False)
parser.add_argument("contract_source", type=_path_validator)
parser.add_argument("contract_name", default="Wallet")
args = parser.parse_args()
contract_name = args.contract_name
contract_path = args.contract_source

# set up logging
if args.verbose:
    __logfmt = "%(asctime)s %(levelname)s %(message)s [in %(funcName)s %(filename)s:%(lineno)d]"
else:
    __logfmt = "%(asctime)s %(levelname)s %(message)s"
logging.basicConfig(filename="evmpatch-deploy.log",
                    level=(logging.DEBUG),
                    format=__logfmt)
coloredlogs.install(level=('DEBUG' if args.verbose else 'INFO'), fmt=__logfmt)

if not contract_path.exists():
    log.error("No such file '%s'", contract_path)
    sys.exit(-1)

log.info("Starting EVMPatch")

log.info("Compiling Solidity code %s", args.contract_source)
cmd = [
    "solc-v0.6.4", "--overwrite", "--bin", "--bin-runtime", "--abi", "-o", "./",
    args.contract_source
]
# solc_out = ""
# solc_out = sp.check_output(cmd)
solc_out = sp.check_output(cmd).decode()

for required_file in (f"./{contract_name}.bin", f"{contract_name}.bin-runtime",
                      f"{contract_name}.abi"):
    if not os.path.exists(required_file):
        log.error(
            "Compilation error: solc didn't produce required file %s\nsolc output:\n%s",
            required_file, solc_out)
        parser.print_help()
        sys.exit(-1)

with open(f"./{contract_name}.bin") as f:
    contract_constructor = binascii.unhexlify(f.read())
with open(f"./{contract_name}.bin-runtime") as f:
    contract_runtime = binascii.unhexlify(f.read())
with open(f"./{contract_name}.abi") as f:
    contract_abi = json.load(f)

log.info("Invoking Bytecode Rewriter")
constructor_args = b""
assert (len([
    x for x in contract_abi if x['type'] == 'constructor'
][0]['inputs']) == 0), "constructor argument currently unsupported!"
proxy_constructor = evmpatch.deploy.proxy_deploy(contract_constructor,
                                                 constructor_args)
logic_constructor = evmpatch.deploy.make_deployable(contract_runtime)

log.info("Connecting to local Ethereum Node")
_ipcpath = "/home/user/.geth.ipc"
if os.path.exists('/tmp/geth/geth.ipc'):
    _ipcpath = '/tmp/geth/geth.ipc'
w3 = web3.Web3(web3.IPCProvider(_ipcpath))
# web3py >=5
w3.middleware_onion.inject(web3.middleware.geth_poa_middleware, layer=0)
# web3py <5
# w3.middleware_stack.inject(web3.middleware.geth_poa_middleware, layer=0)

assert w3.isConnected(), "Web3 API not connected to Ethereum client!"
log.info("Connect to Ethereum Node %s", w3.clientVersion)
account = w3.eth.accounts[0]
log.debug("using account %s", account)
w3.eth.defaultAccount = w3.eth.accounts[0]

log.info("Deploying Upgradable Contract")
proxy_contract = w3.eth.contract(
    bytecode=proxy_constructor,
    abi=(contract_abi + evmpatch.proxycontract.PROXY_ABI),
)
logic_contract = w3.eth.contract(bytecode=logic_constructor, abi=contract_abi)

log.info("Deploying Logic Contract")
tx_hash = logic_contract.constructor().transact()
tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)
log.debug("%s", tx_receipt)
logic_address = tx_receipt.contractAddress
log.debug("logic address: %s", logic_address)
log.info("Deploying Proxy Contract")
tx_hash = proxy_contract.constructor().transact()
tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)
log.debug("%s", tx_receipt)

proxy_contract = w3.eth.contract(
    address=tx_receipt.contractAddress,
    abi=(contract_abi + evmpatch.proxycontract.PROXY_ABI),
)

log.info("Setting logic contract address")
tx_hash = proxy_contract.functions['__upgrade'](logic_address).transact()
tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)

if 'deposit' in proxy_contract.functions and 'withdraw' in proxy_contract.functions:
    log.info("Testing deposit/withdraw of Wallet contract")

    tx_hash = proxy_contract.functions.deposit().transact({'value': 10})
    tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)
    tx_hash = proxy_contract.functions.withdraw(5).transact()
    tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)

    balance = w3.eth.getBalance(proxy_contract.address)
    if balance == 5:
        log.info("testing succeeded")
    else:
        log.error(
            "deposit().value(10); withdraw(5); expected balance==5; got balance of %s",
            balance)
else:
    log.warning("Cannot test contract functionality!")

log.info("Regular Exit - Bye Bye")
