import ctypes
import logging

import pytest

import ads_async
from ads_async import constants, protocol, structs

logger = logging.getLogger(__name__)


@pytest.fixture(scope='module')
def tmc_filename() -> str:
    return 'kmono.tmc'


@pytest.fixture(scope='function')
def tmc_database(tmc_filename, caplog) -> ads_async.symbols.TmcDatabase:
    return ads_async.symbols.TmcDatabase(tmc_filename)


@pytest.fixture(scope='function')
def symbol(tmc_database) -> ads_async.symbols.Symbol:
    """Just one symbol from the TMC database."""
    for data_area in tmc_database.data_areas:
        for name, symbol in data_area.symbols.items():
            yield symbol
            return


@pytest.fixture(scope='function')
def server(tmc_database) -> protocol.Server:
    return ads_async.protocol.Server(tmc_database)


@pytest.fixture(scope='function')
def client(server) -> protocol.AcceptedClient:
    return server.add_client(('server_host', 0), ('client_addr', 0))


def test_smoke_basic(server: protocol.Server):
    assert isinstance(server.ads_state, constants.AdsState)
    assert len(server.version) == 3
    assert server.name


def serialize_request(command: structs._AdsStructBase,
                      invoke_id: int = 0,
                      command_id: constants.AdsCommandId = None
                      ) -> bytes:
    if command is None:
        serialized = b''
        assert command_id is not None
    else:
        serialized = command.serialize()
        command_id = command.command_id()

    aoe = structs.AoEHeader.create_request(
        source=structs.AmsAddr(structs.AmsNetId((1, 2, 3, 4, 5, 6)), 1),
        target=structs.AmsAddr(structs.AmsNetId((5, 4, 3, 2, 1, 7)), 2),
        command_id=command_id,
        length=len(serialized),
        invoke_id=invoke_id,
    )

    ams = structs.AmsTcpHeader(length=ctypes.sizeof(aoe) + len(serialized))
    logger.debug('Creating frame: %s %s %s', ams, aoe, command)

    parts = [ams, aoe, command or serialized]
    return parts, b''.join(bytes(p) for p in (ams, aoe, serialized))


def send_request(client: protocol.AcceptedClient,
                 command: structs._AdsStructBase = None,
                 command_id: constants.AdsCommandId = None,
                 invoke_id: int = 0):
    commands, serialized = serialize_request(command, command_id=command_id,
                                             invoke_id=invoke_id)
    print('Sending')
    for idx, send in enumerate(commands, 1):
        print('\t', idx, send)
    print()

    for header, item in client.received_data(serialized):
        response = client.handle_command(
            header=header, request=item)
        print('Received')
        print('\t', response)
    return response


def test_read_state(server: protocol.Server, client: protocol.AcceptedClient):
    header, response = send_request(
        client, command_id=constants.AdsCommandId.READ_STATE)
    assert isinstance(response, structs.AdsReadStateResponse)
    assert response.ads_state == server.ads_state


def test_read_device_info(server: protocol.Server,
                          client: protocol.AcceptedClient):
    header, response = send_request(
        client, command_id=constants.AdsCommandId.READ_DEVICE_INFO)
    assert isinstance(response, structs.AdsDeviceInfo)
    assert response.name == server.name
    assert response.version_tuple == server.version


def test_read_and_write_by_handle(
        server: protocol.Server, client: protocol.AcceptedClient,
        symbol: ads_async.symbols.Symbol):
    request = structs.AdsReadWriteRequest.create_handle_by_name_request(
        symbol.name)
    assert server.database.get_symbol_by_name(symbol.name) is symbol
    response, = send_request(client, command=request)
    assert isinstance(response, structs.AoEHandleResponse)
    handle = response.handle

    request = structs.AdsWriteRequest(
        constants.AdsIndexGroup.SYM_VALBYHND,
        handle,
        data=symbol.ctypes_data_type(1),
    )

    response, = send_request(client, command=request)
    assert isinstance(response, structs.AoEResponseHeader)
    assert response.result == 0

    request = structs.AdsReadRequest(
        constants.AdsIndexGroup.SYM_VALBYHND,
        handle,
        length=symbol.size,
    )

    response, = send_request(client, command=request)
    assert isinstance(response, structs.AoEReadResponse)

    assert response.data == bytes(symbol.ctypes_data_type(1))
