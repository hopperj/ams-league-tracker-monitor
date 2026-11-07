"""Compare field names and dimensions to your installed AMS2 SDK header."""
import argparse
import ctypes as C
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ams_recorder.schema import SharedMemory, ParticipantInfo, STRUCT_BYTES, SEQUENCE_OFFSET

parser=argparse.ArgumentParser()
parser.add_argument("header",type=Path)
args=parser.parse_args()
source=args.header.read_text(encoding="utf-8-sig")
match=re.search(r"SHARED_MEMORY_VERSION\s*=\s*(\d+)",source)
if not match or int(match[1])!=14:parser.exit(1,"Unsupported SDK version; recorder will archive raw captures only.\n")
structures=re.findall(r"typedef\s+struct\s*\{(.*?)\}\s*(\w+)\s*;",source,re.S)
constants={"TYRE_MAX":4,"VEC_MAX":3}
for key in ("STRING_LENGTH_MAX","STORED_PARTICIPANTS_MAX","TYRE_COMPOUND_NAME_LENGTH_MAX"):
    value=re.search(r"\b"+key+r"\s*=\s*(\d+)",source)
    if not value:parser.exit(1,"Missing SDK constant: "+key+"\n")
    constants[key]=int(value[1])
types={C.c_bool:"bool",C.c_char:"char",C.c_float:"float",C.c_uint32:"unsigned int",C.c_int32:"int",ParticipantInfo:"ParticipantInfo"}
for body,name in structures:
    if name not in ("ParticipantInfo","SharedMemory"):continue
    fields=re.findall(r"(unsigned int|int|float|bool|char|ParticipantInfo)\s+(m\w+)\s*((?:\[[\w\d]+\])*)\s*;",re.sub(r"//[^\n]*","",body))
    expected=[]
    for typ,field,dims in fields:
        lengths=[]
        for dim in re.findall(r"\[(\w+)\]",dims):
            lengths.append(int(dim) if dim.isdigit() else constants[dim])
        expected.append((field,typ,lengths))
    cls=ParticipantInfo if name=="ParticipantInfo" else SharedMemory
    actual=[]
    for field,typ in cls._fields_:
        lengths=[]
        while issubclass(typ,C.Array):
            lengths.append(typ._length_);typ=typ._type_
        actual.append((field,types[typ],lengths))
    if expected!=actual:parser.exit(1,"SDK types, field order, or dimensions differ; raw archiving is safe but this decoder needs updating.\n")
print("SDK v14 field types, order, and dimensions match: %d bytes, sequence offset %d."%(STRUCT_BYTES,SEQUENCE_OFFSET))
