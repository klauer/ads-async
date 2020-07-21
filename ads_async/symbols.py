import ctypes
import enum
import logging
import typing

from . import constants, structs
from .constants import AdsDataType

try:
    import pytmc
except ImportError:
    pytmc = None


logger = logging.getLogger(__name__)


class PlcMemory:
    """
    Contiguous block of PLC memory, for use by symbols.
    """

    def __init__(self, size):
        self.memory = bytearray(size)

    def read(self, offset, size):
        return memoryview(self.memory)[offset:offset + size]

    def write(self, offset, data):
        size = len(data)
        self.memory[offset:offset + size] = data


class Symbol:
    name: str
    data_type: AdsDataType
    data_area: 'DataArea'
    ctypes_data_type: typing.Union[typing.Type[ctypes.Array],
                                   typing.Type[ctypes._SimpleCData]]
    size: int
    array_length: int

    def __init__(self,
                 name: str,
                 offset: int,
                 data_type: constants.AdsDataType,
                 array_length: int,
                 data_area: 'DataArea'
                 ):
        self.array_length = array_length
        self.data_area = data_area
        self.data_type = data_type
        self.name = name
        self.offset = offset

        self._configure_data_type()

    @property
    def memory(self):
        return self.data_area.memory

    @staticmethod
    def from_tmc_symbol(
            tmc_symbol: 'pytmc.parser.symbol',
            data_area: 'DataArea',
            ) -> 'Symbol':
        info = tmc_symbol.info
        bit_offset = int(info['bit_offs'])
        if (bit_offset % 8) != 0:
            raise ValueError('Symbol not byte-aligned?')

        offset = bit_offset // 8
        type_name = info['type']
        if type_name.startswith('STRING') and '(' in type_name:
            array_length = int(type_name.split('(')[1].rstrip(')'))
            type_name = 'STRING'
        else:
            array_length = getattr(tmc_symbol.array_info, 'elements', 1)

        try:
            data_type = TmcTypes[type_name].value
        except KeyError:
            # assert tmc_symbol.data_type.is_complex_type
            raise ValueError(
                f'Complex types not yet supported: {info["type"]}'
            ) from None

        symbol = Symbol(
            tmc_symbol.name,
            data_area=data_area,
            offset=offset,
            data_type=data_type,
            array_length=array_length,
        )
        if hasattr(tmc_symbol, 'Comment'):
            symbol.__doc__ = tmc_symbol.Comment[0].text

        return symbol

    def _configure_data_type(self):
        ctypes_base_type = self.data_type.ctypes_type
        if self.array_length > 1:
            self.ctypes_data_type = self.array_length * ctypes_base_type
        else:
            self.ctypes_data_type = ctypes_base_type
        self.size = ctypes.sizeof(self.ctypes_data_type)

    def read(self):
        raw = self.memory.read(self.offset, self.size)
        return self.ctypes_data_type.from_buffer(raw)

    def write(self, value):
        if not isinstance(value, ctypes._SimpleCData):
            if isinstance(value, bytes):
                consumed, value = structs.deserialize_data(
                    data_type=self.data_type, data=value,
                    length=self.array_length,
                )
            else:
                value = self.ctypes_data_type(value)

        logger.debug('Symbol %s write %s', self, value)
        return self.memory.write(self.offset, bytes(value))

    @property
    def value(self):
        return self.read()

    def __repr__(self):
        return (
            f'<{self.__class__.__name__} name={self.name!r} '
            f'value={self.value}>'
        )


class DataArea:
    memory: PlcMemory
    index_group: constants.AdsIndexGroup
    symbols: typing.Dict[str, Symbol]
    area_type: str

    def __init__(self, index_group: constants.AdsIndexGroup,
                 area_type: str,
                 *,
                 memory: PlcMemory = None,
                 memory_size: typing.Optional[int] = None):

        self.index_group = index_group
        self.area_type = area_type
        self.symbols = {}

        if memory is not None:
            self.memory = memory
        elif memory_size is not None:
            self.memory = PlcMemory(memory_size)
        else:
            raise ValueError('Must specify either memory or memory_size')


class TmcDataArea(DataArea):
    def add_symbol(self, tmc_symbol: 'pytmc.parser.Symbol'):
        try:
            symbol = Symbol.from_tmc_symbol(tmc_symbol,
                                            data_area=self)  # type: Symbol
        except ValueError as ex:
            logger.debug(str(ex))
            return

        self.symbols[tmc_symbol.name] = symbol
        return symbol

    def __repr__(self):
        return f'<{self.__class__.__name__} {self.area_type}>'


class TmcTypes(enum.Enum):
    BOOL = AdsDataType.BIT
    BYTE = AdsDataType.UINT8
    SINT = AdsDataType.INT8
    USINT = AdsDataType.UINT8

    WORD = AdsDataType.UINT16
    INT = AdsDataType.INT16
    UINT = AdsDataType.UINT16

    DWORD = AdsDataType.UINT32
    DINT = AdsDataType.INT32
    UDINT = AdsDataType.UINT32

    ENUM = AdsDataType.UINT32

    LINT = AdsDataType.INT64
    ULINT = AdsDataType.UINT64

    REAL = AdsDataType.REAL32
    LREAL = AdsDataType.REAL64

    STRING = AdsDataType.STRING


class DataAreaIndexGroup(enum.Enum):
    # <AreaNo AreaType="Internal" CreateSymbols="true">3</AreaNo>
    # e.g., Global_Version.stLibVersion_Tc2_System
    Internal = constants.AdsIndexGroup.PLC_DATA_AREA  # 0x4040

    # <AreaNo AreaType="InputDst" CreateSymbols="true">0</AreaNo>
    # read/write input byte(s)
    InputDst = constants.AdsIndexGroup.IOIMAGE_RWIB  # 0xF020

    # <AreaNo AreaType="OutputSrc" CreateSymbols="true">1</AreaNo>
    # read/write output byte(s)
    # e.g., Axis.PlcToNc
    OutputSrc = constants.AdsIndexGroup.IOIMAGE_RWOB  # 0xF030

    # TODO:
    # Standard
    # InputSrc
    # OutputDst
    # MArea
    # RetainSrc
    # RetainDst
    # InfoData
    # RedundancySrc
    # RedundancyDst


class Database:
    data_areas: typing.List[DataArea]
    index_groups: typing.Dict[constants.AdsIndexGroup, DataArea]

    def get_symbol_by_name(self, symbol_name) -> Symbol:
        raise KeyError(symbol_name)


class TmcDatabase(Database):
    tmc: 'pytmc.parser.TcModuleClass'

    def __init__(self, tmc):
        super().__init__()

        if pytmc is None:
            raise RuntimeError('pytmc unavailable for .tmc file support')

        if not isinstance(tmc, pytmc.parser.TcModuleClass):
            logger.debug('Loading tmc file: %s', tmc)
            tmc = pytmc.parser.parse(tmc)

        self.tmc = tmc
        self.data_areas = []
        self.index_groups = {}
        self._load_data_areas()

    def get_symbol_by_name(self, symbol_name: str) -> Symbol:
        for data_area in self.data_areas:
            try:
                return data_area.symbols[symbol_name]
            except KeyError:
                ...

        raise KeyError(symbol_name)

    def _load_data_areas(self):
        for tmc_area in self.tmc.find(pytmc.parser.DataArea):
            info = tmc_area.AreaNo[0].attributes
            area_type = info['AreaType']
            create_symbols = info.get('CreateSymbols', 'true')
            byte_size = int(tmc_area.ByteSize[0].text)
            if create_symbols != 'true':
                continue

            index_group = DataAreaIndexGroup[area_type]
            area = self.add_data_area(
                index_group, TmcDataArea(index_group, area_type,
                                         memory_size=byte_size))

            for sym in tmc_area.find(pytmc.parser.Symbol):
                area.add_symbol(sym)

        self._configure_plc_memory_area()

    def add_data_area(self, index_group: constants.AdsIndexGroup,
                      area: DataArea) -> DataArea:
        self.data_areas.append(area)
        self.index_groups[index_group] = area
        return area

    def _configure_plc_memory_area(self):
        if constants.AdsIndexGroup.PLC_MEMORY_AREA in self.index_groups:
            return

        index_group = constants.AdsIndexGroup.PLC_MEMORY_AREA
        self.add_data_area(
            index_group, DataArea(index_group, 'PLC_MEMORY_AREA',
                                  memory_size=100_000))
