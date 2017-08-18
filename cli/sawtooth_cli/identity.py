# Copyright 2017 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ------------------------------------------------------------------------------

from base64 import b64decode
import csv
import getpass
import hashlib
import json
import os
import sys
import time
import yaml

from sawtooth_cli.exceptions import CliException
from sawtooth_cli.rest_client import RestClient
from sawtooth_cli import tty

from sawtooth_cli.protobuf.identities_pb2 import IdentityPayload
from sawtooth_cli.protobuf.identity_pb2 import Policy
from sawtooth_cli.protobuf.identity_pb2 import PolicyList
from sawtooth_cli.protobuf.identity_pb2 import Role
from sawtooth_cli.protobuf.identity_pb2 import RoleList
from sawtooth_cli.protobuf.transaction_pb2 import TransactionHeader
from sawtooth_cli.protobuf.transaction_pb2 import Transaction
from sawtooth_cli.protobuf.batch_pb2 import BatchHeader
from sawtooth_cli.protobuf.batch_pb2 import Batch
from sawtooth_cli.protobuf.batch_pb2 import BatchList

import sawtooth_signing as signing


IDENTITY_NAMESPACE = '00001d'

_MIN_PRINT_WIDTH = 15
_MAX_KEY_PARTS = 4
_FIRST_ADDRESS_PART_SIZE = 14
_ADDRESS_PART_SIZE = 16
_POLICY_PREFIX = "00"
_ROLE_PREFIX = "01"
_EMPTY_PART = hashlib.sha256("".encode()).hexdigest()[:_ADDRESS_PART_SIZE]


def add_identity_parser(subparsers, parent_parser):
    """Creates the arg parsers needed for the identity command and
    its subcommands.
    """
    parser = subparsers.add_parser('identity')

    identity_parsers = parser.add_subparsers(title="subcommands",
                                             dest="subcommand")
    identity_parsers.required = True

    policy_parser = identity_parsers.add_parser('policy')
    policy_parsers = policy_parser.add_subparsers(
        title='policy',
        dest='policy_cmd')
    policy_parsers.required = True

    create_parser = policy_parsers.add_parser(
        'create',
        help='creates batches of sawtooth-identity transactions for setting a '
        'policy')

    create_parser.add_argument(
        '-k', '--key',
        type=str,
        help='the signing key for the resulting batches')

    create_target_group = create_parser.add_mutually_exclusive_group()
    create_target_group.add_argument(
        '-o', '--output',
        type=str,
        help='the name of the file to ouput the resulting batches')

    create_target_group.add_argument(
        '--url',
        type=str,
        help="the URL of a validator's REST API",
        default='http://localhost:8080')

    create_parser.add_argument(
        'name',
        type=str,
        help='The name of the new policy')

    create_parser.add_argument(
        'rule',
        type=str,
        nargs="+",
        help='Each rule should be in the following format "PERMIT_KEY <key>"'
        ' or "DENY_KEY <key>". Multiple "rule" arguments can be added.')

    list_parser = policy_parsers.add_parser(
        'list',
        help='list the current policies')

    list_parser.add_argument(
        '--url',
        type=str,
        help="the URL of a validator's REST API",
        default='http://localhost:8080')

    list_parser.add_argument(
        '--format',
        default='default',
        choices=['default', 'csv', 'json', 'yaml'],
        help='the format of the output')

    role_parser = identity_parsers.add_parser('role')
    role_parsers = role_parser.add_subparsers(
        title='role',
        dest='role_cmd')
    role_parsers.required = True

    create_parser = role_parsers.add_parser(
        'create',
        help='creates batches of sawtooth-identity transactions for setting a '
        'role')

    create_parser.add_argument(
        '-k', '--key',
        type=str,
        help='the signing key for the resulting batches')

    create_target_group = create_parser.add_mutually_exclusive_group()
    create_target_group.add_argument(
        '-o', '--output',
        type=str,
        help='the name of the file to ouput the resulting batches')

    create_target_group.add_argument(
        '--url',
        type=str,
        help="the URL of a validator's REST API",
        default='http://localhost:8080')

    create_parser.add_argument(
        'name',
        type=str,
        help='The name of the role')

    create_parser.add_argument(
        'policy',
        type=str,
        help='the name of the policy the role will be restricted to.')

    list_parser = role_parsers.add_parser(
        'list',
        help='list the current keys and values of roles')

    list_parser.add_argument(
        '--url',
        type=str,
        help="the URL of a validator's REST API",
        default='http://localhost:8080')

    list_parser.add_argument(
        '--format',
        default='default',
        choices=['default', 'csv', 'json', 'yaml'],
        help='the format of the output')


def do_identity(args):
    """Executes the config commands subcommands.
    """
    if args.subcommand == 'policy' and args.policy_cmd == 'create':
        _do_identity_policy_create(args)
    elif args.subcommand == 'policy' and args.policy_cmd == 'list':
        _do_identity_policy_list(args)
    elif args.subcommand == 'role' and args.role_cmd == 'create':
        _do_identity_role_create(args)
    elif args.subcommand == 'role' and args.role_cmd == 'list':
        _do_identity_role_list(args)
    else:
        raise AssertionError(
            '"{}" is not a valid subcommand of "identity"'.format(
                args.subcommand))


def _do_identity_policy_create(args):
    """Executes the 'policy create' subcommand.  Given a key file, and a
    series of entries, it generates a batch of sawtooth_identity
    transactions in a BatchList instance. The BatchList is either stored to a
    file or submitted to a validator, depending on the supplied CLI arguments.
    """
    pubkey, signing_key = _read_signing_keys(args.key)

    txns = [_create_policy_txn(pubkey, signing_key, args.name,
            args.rule)]

    batch = _create_batch(pubkey, signing_key, txns)

    batch_list = BatchList(batches=[batch])

    if args.output is not None:
        try:
            with open(args.output, 'wb') as batch_file:
                batch_file.write(batch_list.SerializeToString())
        except IOError as e:
            raise CliException(
                'Unable to write to batch file: {}'.format(str(e)))
    elif args.url is not None:
        rest_client = RestClient(args.url)
        rest_client.send_batches(batch_list)
    else:
        raise AssertionError('No target for create set.')


def _do_identity_policy_list(args):
    rest_client = RestClient(args.url)
    state = rest_client.list_state(subtree=IDENTITY_NAMESPACE + _POLICY_PREFIX)

    head = state['head']
    state_values = state['data']
    printable_policies = []
    for state_value in state_values:
        policies_list = PolicyList()
        decoded = b64decode(state_value['data'])
        policies_list.ParseFromString(decoded)

        for policy in policies_list.policies:
            printable_policies.append(policy)

    printable_policies.sort(key=lambda p: p.name)

    if args.format == 'default':
        tty_width = tty.width()
        for policy in printable_policies:
            # Set value width to the available terminal space, or the min width
            width = tty_width - len(policy.name) - 3
            width = width if width > _MIN_PRINT_WIDTH else _MIN_PRINT_WIDTH
            value = "Entries:\n"
            for entry in policy.entries:
                entry_string = (" " * 4) + Policy.Type.Name(entry.type) + " " \
                    + entry.key
                value += (entry_string[:width] + '...'
                          if len(entry_string) > width
                          else entry_string) + "\n"
            print('{}: \n  {}'.format(policy.name, value))
    elif args.format == 'csv':
        try:
            writer = csv.writer(sys.stdout, quoting=csv.QUOTE_ALL)
            writer.writerow(['POLICY NAME', 'ENTRIES'])
            for policy in printable_policies:
                output = [policy.name]
                for entry in policy.entries:
                    output.append(Policy.Type.Name(entry.type) + " " +
                                  entry.key)
                writer.writerow(output)
        except csv.Error:
            raise CliException('Error writing CSV')
    elif args.format == 'json' or args.format == 'yaml':
        output = {}
        for policy in printable_policies:
            value = "Entries: "
            for entry in policy.entries:
                entry_string = Policy.Type.Name(entry.type) + " " \
                    + entry.key
                value += entry_string + " "
            output[policy.name] = value

        policies_snapshot = {
            'head': head,
            'policies': output
        }
        if args.format == 'json':
            print(json.dumps(policies_snapshot, indent=2, sort_keys=True))
        else:
            print(yaml.dump(policies_snapshot, default_flow_style=False)[0:-1])
    else:
        raise AssertionError('Unknown format {}'.format(args.format))


def _do_identity_role_create(args):
    """Executes the 'role create' subcommand.  Given a key file, a role name,
    and a policy name it generates a batch of sawtooth_identity
    transactions in a BatchList instance. The BatchList is either stored to a
    file or submitted to a validator, depending on the supplied CLI arguments.
    """
    pubkey, signing_key = _read_signing_keys(args.key)
    txns = [_create_role_txn(pubkey, signing_key, args.name,
            args.policy)]

    batch = _create_batch(pubkey, signing_key, txns)

    batch_list = BatchList(batches=[batch])

    if args.output is not None:
        try:
            with open(args.output, 'wb') as batch_file:
                batch_file.write(batch_list.SerializeToString())
        except IOError as e:
            raise CliException(
                'Unable to write to batch file: {}'.format(str(e)))
    elif args.url is not None:
        rest_client = RestClient(args.url)
        rest_client.send_batches(batch_list)
    else:
        raise AssertionError('No target for create set.')


def _do_identity_role_list(args):
    """Lists the current on-chain configuration values.
    """
    rest_client = RestClient(args.url)
    state = rest_client.list_state(subtree=IDENTITY_NAMESPACE + _ROLE_PREFIX)

    head = state['head']
    state_values = state['data']
    printable_roles = []
    for state_value in state_values:
        role_list = RoleList()
        decoded = b64decode(state_value['data'])
        role_list.ParseFromString(decoded)

        for role in role_list.roles:
            printable_roles.append(role)

    printable_roles.sort(key=lambda r: r.name)

    if args.format == 'default':
        tty_width = tty.width()
        for role in printable_roles:
            # Set value width to the available terminal space, or the min width
            width = tty_width - len(role.name) - 3
            width = width if width > _MIN_PRINT_WIDTH else _MIN_PRINT_WIDTH
            value = (role.policy_name[:width] + '...'
                     if len(role.policy_name) > width
                     else role.policy_name)
            print('{}: {}'.format(role.name, value))
    elif args.format == 'csv':
        try:
            writer = csv.writer(sys.stdout, quoting=csv.QUOTE_ALL)
            writer.writerow(['KEY', 'VALUE'])
            for role in printable_roles:
                writer.writerow([role.name, role.policy_name])
        except csv.Error:
            raise CliException('Error writing CSV')
    elif args.format == 'json' or args.format == 'yaml':
        roles_snapshot = {
            'head': head,
            'roles': {role.name: role.policy_name
                      for role in printable_roles}
        }
        if args.format == 'json':
            print(json.dumps(roles_snapshot, indent=2, sort_keys=True))
        else:
            print(yaml.dump(roles_snapshot, default_flow_style=False)[0:-1])
    else:
        raise AssertionError('Unknown format {}'.format(args.format))


def _create_policy_txn(pubkey, signing_key, policy_name, rules):
    entries = []
    for rule in rules:
        rule = rule.split(" ")
        if rule[0] == "PERMIT_KEY":
            entry = Policy.Entry(type=Policy.PERMIT_KEY,
                                 key=rule[1])
            entries.append(entry)
        elif rule[0] == "DENY_KEY":
            entry = Policy.Entry(type=Policy.DENY_KEY,
                                 key=rule[1])
            entries.append(entry)
    policy = Policy(name=policy_name, entries=entries)
    payload = IdentityPayload(type=IdentityPayload.POLICY,
                              data=policy.SerializeToString())

    policy_address = _policy_to_address(policy_name)

    header = TransactionHeader(
        signer_pubkey=pubkey,
        family_name='sawtooth_identity',
        family_version='1.0',
        inputs=[policy_address],
        outputs=[policy_address],
        dependencies=[],
        payload_encoding="application/protobuf",
        payload_sha512=hashlib.sha512(
            payload.SerializeToString()).hexdigest(),
        batcher_pubkey=pubkey,
        nonce=time.time().hex().encode())

    header_bytes = header.SerializeToString()

    signature = signing.sign(header_bytes, signing_key)

    transaction = Transaction(
        header=header_bytes,
        payload=payload.SerializeToString(),
        header_signature=signature)

    return transaction


def _create_role_txn(pubkey, signing_key, role_name, policy_name):
    role = Role(name=role_name, policy_name=policy_name)
    payload = IdentityPayload(type=IdentityPayload.ROLE,
                              data=role.SerializeToString())

    policy_address = _policy_to_address(policy_name)
    role_address = _role_to_address(role_name)

    header = TransactionHeader(
        signer_pubkey=pubkey,
        family_name='sawtooth_identity',
        family_version='1.0',
        inputs=[policy_address, role_address],
        outputs=[role_address],
        dependencies=[],
        payload_encoding="application/protobuf",
        payload_sha512=hashlib.sha512(
            payload.SerializeToString()).hexdigest(),
        batcher_pubkey=pubkey,
        nonce=time.time().hex().encode())

    header_bytes = header.SerializeToString()

    signature = signing.sign(header_bytes, signing_key)

    transaction = Transaction(
        header=header_bytes,
        payload=payload.SerializeToString(),
        header_signature=signature)

    return transaction


def _read_signing_keys(key_filename):
    """Reads the given file as a WIF formatted key.

    Args:
        key_filename: The filename where the key is stored. If None,
            defaults to the default key for the current user.

    Returns:
        tuple (str, str): the public and private key pair

    Raises:
        CliException: If unable to read the file.
    """
    filename = key_filename
    if filename is None:
        filename = os.path.join(os.path.expanduser('~'),
                                '.sawtooth',
                                'keys',
                                getpass.getuser() + '.priv')

    try:
        with open(filename, 'r') as key_file:
            signing_key = key_file.read().strip()
            pubkey = signing.generate_pubkey(signing_key)

            return pubkey, signing_key
    except IOError as e:
        raise CliException('Unable to read key file: {}'.format(str(e)))


def _create_batch(pubkey, signing_key, transactions):
    """Creates a batch from a list of transactions and a public key, and signs
    the resulting batch with the given signing key.

    Args:
        pubkey (str): The public key associated with the signing key.
        signing_key (str): The private key for signing the batch.
        transactions (list of `Transaction`): The transactions to add to the
            batch.

    Returns:
        `Batch`: The constructed and signed batch.
    """
    txn_ids = [txn.header_signature for txn in transactions]
    batch_header = BatchHeader(signer_pubkey=pubkey,
                               transaction_ids=txn_ids).SerializeToString()

    return Batch(
        header=batch_header,
        header_signature=signing.sign(batch_header, signing_key),
        transactions=transactions
    )


def _to_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _role_to_address(role_name):
    # split the key into 4 parts, maximum
    key_parts = role_name.split('.', maxsplit=_MAX_KEY_PARTS - 1)

    # compute the short hash of each part
    addr_parts = [_to_hash(key_parts[0])[:_FIRST_ADDRESS_PART_SIZE]]
    addr_parts += [_to_hash(x)[:_ADDRESS_PART_SIZE] for x in
                   key_parts[1:]]
    # pad the parts with the empty hash, if needed
    addr_parts.extend([_EMPTY_PART] * (_MAX_KEY_PARTS - len(addr_parts)))
    return IDENTITY_NAMESPACE + _ROLE_PREFIX + ''.join(addr_parts)


def _policy_to_address(policy_name):
    return IDENTITY_NAMESPACE + _POLICY_PREFIX + \
        _to_hash(policy_name)[:62]