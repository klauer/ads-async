import pytest

import ads_async


@pytest.fixture(scope='function')
def memory():
    return ads_async.symbols.PlcMemory(1000)


def test_symbol(memory):
    sym = ads_async.symbols.Symbol(
        group=ads_async.constants.AdsIndexGroup.PLC_DATA_AREA,
        offset=0,
        data_type=ads_async.constants.AdsDataType.INT32,
        array_length=1,
        memory=memory)

    value = 0x2345
    sym.write(value)
    assert sym.read()[0] == value
    assert sym.value[0] == value
