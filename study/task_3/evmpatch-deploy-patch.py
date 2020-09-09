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
import yaml
from eth_utils import encode_hex, to_wei

import evmpatch

# from web3.auto.gethdev import w3


def _path_validator(p):
    path = pathlib.Path(p)
    assert path.exists()
    return path


# parse arguments
parser = argparse.ArgumentParser(
    description="EVMPatch Command Line Interface for Contract Deployment")
parser.add_argument("--verbose", "-v", action="store_true", default=False)
parser.add_argument("contract_source", type=_path_validator)
parser.add_argument("contract_name", default="Wallet")
parser.add_argument("patch_spec", default="Path.yaml", type=_path_validator)
args = parser.parse_args()
contract_name = args.contract_name
contract_path = args.contract_source

# set up logging
log = logging.getLogger()
if args.verbose:
    __logfmt = "%(asctime)s %(levelname)s %(message)s [in %(funcName)s %(filename)s:%(lineno)d]"
else:
    __logfmt = "%(asctime)s %(levelname)s %(message)s"
logging.basicConfig(filename="evmpatch-deploy-patch.log",
                    level=(logging.DEBUG),
                    format=__logfmt)
coloredlogs.install(level=('DEBUG' if args.verbose else 'INFO'), fmt=__logfmt)

log.info("Starting EVMPatch")

log.info("Compiling Solidity code %s", args.contract_source)
cmd = [
    "solc-v0.6.4", "--overwrite", "--bin", "--bin-runtime", "--abi",
    "--combined-json", "abi,hashes,srcmap-runtime", "-o", "./", args.contract_source
]
# solc_out = ""
try:
    solc_out = sp.check_output(cmd).decode()
except Exception as e:
    log.exception("excepted occured while executing solc: %r", e)
    raise

for required_file in (f"./{contract_name}.bin", f"{contract_name}.bin-runtime",
                      f"{contract_name}.abi", "combined.json"):
    if not os.path.exists(required_file):
        log.error(
            "Compilation error: solc didn't produce required file %s\nsolc output:\n%s",
            required_file, solc_out)

with open(f"./{contract_name}.bin") as f:
    contract_constructor = binascii.unhexlify(f.read())
with open(f"./{contract_name}.bin-runtime") as f:
    contract_runtime = binascii.unhexlify(f.read())
with open(f"./{contract_name}.abi") as f:
    contract_abi = json.load(f)
with open("combined.json") as f:
    combined_metadata = json.load(f)

for cname, cmeta in combined_metadata['contracts'].items():
    if contract_name in cname:
        source_map = cmeta['srcmap-runtime']
        contract_hashes = cmeta['hashes']
        break

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

assert 'deposit' in proxy_contract.functions and 'withdraw' in proxy_contract.functions, "Can only test Wallet contract with deposit/withdraw functions!"


def test():
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


test()


def attack():
    log.info("Testing attack against Wallet contract")

    # create new attacker account
    w3.geth.personal.newAccount('')
    sender = w3.eth.accounts[-1]
    log.debug("using sender %s", sender)
    log.debug("contract owner is %s", w3.eth.accounts[0])
    w3.geth.personal.unlockAccount(sender, '')
    # give some funds to attacker account
    tx_hash = w3.eth.sendTransaction({
        'from': w3.eth.accounts[0],
        'to': sender,
        'value': to_wei(100, 'ether')
    })
    w3.eth.waitForTransactionReceipt(tx_hash)

    balance = w3.eth.getBalance(proxy_contract.address)
    if balance == 0:
        log.debug("giving Wallet a little ether")
        # give some funds to wallet
        tx_hash = proxy_contract.functions.deposit().transact({'value': 10})
        tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)

    try:
        log.debug("sending attack TX")
        tx_hash = proxy_contract.functions.migrateTo(sender).transact(
            {'from': sender})
        tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)
    except ValueError as ex:
        log.debug("got exception %s", ex, exc_info=sys.exc_info())
        if "always failing" not in str(ex):
            raise

    balance = w3.eth.getBalance(proxy_contract.address)
    if balance == 0:
        log.info("attack succeeded")
        return True
    else:
        log.info("attack failed")
        return False


assert attack()

log.info("Parsing patch specification")

with open(args.patch_spec) as f:
    patch_spec = yaml.safe_load(f)

with open(contract_path) as f:
    contract_source = f.read()
functions_starts = evmpatch.tools.parse_functions_from_source_map(
    contract_runtime, contract_source, source_map, contract_hashes)
log.debug("%s", functions_starts)

rw = evmpatch.BBJumpoutRewriter(contract_runtime)

for function, patches in patch_spec['add_require_patch'].items():
    if function not in functions_starts:
        log.error("Function %s not in set of possible functions: %s", function,
                  set(functions_starts.keys()))
        sys.exit(1)
    for patch_str in patches:
        log.debug("original patch string: %r", patch_str)
        patch_str = patch_str.replace('owner', '0')
        patch_str = patch_str.replace('msg.sender', 'caller()')
        patch_str = patch_str.replace('msg.value', 'callvalue()')
        log.debug("applying require patch: %r", patch_str)
        patch = evmpatch.patches.require_patch(patch_str)
        patch_point = functions_starts[function]
        rw.insert_patch(patch_point, patch)

for function in patch_spec['delete_function_patch']:
    if function not in functions_starts:
        log.error("Function %s not in set of possible functions: %s", function,
                  set(functions_starts.keys()))
        sys.exit(1)
    patch_point = functions_starts[function]
    rw.insert_patch(patch_point, evmpatch.patches.REVERT_PATCH)

patched_code = rw.get_code()
logic_constructor = evmpatch.deploy.make_deployable(patched_code)
logic_contract = w3.eth.contract(bytecode=logic_constructor, abi=contract_abi)

log.info("Deploying Logic Contract")
tx_hash = logic_contract.constructor().transact()
tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)
log.debug("%s", tx_receipt)
logic_address = tx_receipt.contractAddress

log.info("Setting logic contract address")
tx_hash = proxy_contract.functions['__upgrade'](logic_address).transact()
tx_receipt = w3.eth.waitForTransactionReceipt(tx_hash)

if attack():
    log.error("Patching Failed!")
    sys.exit(-1)
else:
    log.info("patching succeeded! Congratulations!")

log.info("Regular Exit - Bye Bye")
