"""SWG CKAT/0001 decoding and KFAT/0003 reading/writing (no Blender required).

Layout and interpolation follow clientSkeletalAnimation's animation templates;
compressed quaternion expansion follows sharedMath/CompressedQuaternion.cpp.
IFF block sizes are big endian; payload numbers are little endian. SWG does
not add RIFF-style padding to odd-length chunks.
"""

from bisect import bisect_right
from dataclasses import dataclass, field
import math
from pathlib import Path
import struct


class AnimationFormatError(ValueError):
    pass


def normalize(q):
    magnitude = math.sqrt(sum(v * v for v in q))
    if not math.isfinite(magnitude) or magnitude < 1e-12:
        raise AnimationFormatError("Invalid animation quaternion")
    return tuple(v / magnitude for v in q)


def slerp(a, b, factor):
    a, b = normalize(a), normalize(b)
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:
        b, dot = tuple(-v for v in b), -dot
    if dot > 0.9995:
        return normalize(tuple(x + (y - x) * factor for x, y in zip(a, b)))
    theta = math.acos(max(-1.0, min(1.0, dot)))
    denominator = math.sin(theta)
    return tuple((math.sin((1-factor)*theta)*x + math.sin(factor*theta)*y) / denominator
                 for x, y in zip(a, b))


@dataclass
class Channel:
    keys: list
    quaternion: bool = False

    def __post_init__(self):
        if not self.keys:
            raise AnimationFormatError("Animation channel has no keys")
        self.times = [frame for frame, _ in self.keys]
        if any(not math.isfinite(t) or t < 0 for t in self.times) or any(
                a >= b for a, b in zip(self.times, self.times[1:])):
            raise AnimationFormatError("Animation keyframes must be nonnegative and strictly increasing")
        for _, value in self.keys:
            values = value if isinstance(value, (tuple, list)) else (value,)
            if not all(math.isfinite(v) for v in values):
                raise AnimationFormatError("Animation contains nonfinite values")
            if self.quaternion:
                normalize(value)

    def sample(self, frame):
        upper = bisect_right(self.times, frame)
        if upper == 0:
            return self.keys[0][1]
        if upper == len(self.keys):
            return self.keys[-1][1]
        first, a = self.keys[upper-1]
        last, b = self.keys[upper]
        factor = (frame-first)/(last-first)
        if self.quaternion:
            return slerp(a, b, factor)
        if isinstance(a, (tuple, list)):
            return tuple(x+(y-x)*factor for x, y in zip(a,b))
        return a+(b-a)*factor


@dataclass
class Track:
    rotation: Channel
    translations: tuple

    def sample(self, frame):
        return self.rotation.sample(frame), tuple(channel.sample(frame) for channel in self.translations)


@dataclass
class Clip:
    fps: float
    last_frame: int
    tracks: dict
    messages: list = field(default_factory=list)
    locomotion_translation: object = None
    locomotion_rotation: object = None
    average_speed: float = 0.0
    source_format: str = 'KFAT/0003'
    warnings: list = field(default_factory=list)


@dataclass
class Block:
    tag: str
    payload: bytes
    children: object = None


def blocks(data):
    result, offset = [], 0
    while offset < len(data):
        if offset+8 > len(data):
            raise AnimationFormatError("Truncated IFF block header")
        raw_tag, size = struct.unpack_from('>4sI',data,offset)
        end = offset+8+size
        if end > len(data):
            raise AnimationFormatError("IFF block exceeds its container")
        try:
            tag = raw_tag.decode('ascii')
            payload = data[offset+8:end]
            if tag == 'FORM':
                if size < 4:
                    raise AnimationFormatError("Invalid IFF form")
                result.append(Block(payload[:4].decode('ascii'), b'', blocks(payload[4:])))
            else:
                result.append(Block(tag,payload))
        except UnicodeError as error:
            raise AnimationFormatError("Invalid IFF tag") from error
        offset = end
    return result


class Reader:
    def __init__(self, payload):
        self.data, self.offset = payload, 0

    def read(self, fmt):
        size = struct.calcsize('<'+fmt)
        if self.offset+size > len(self.data):
            raise AnimationFormatError("Truncated animation payload")
        values = struct.unpack_from('<'+fmt,self.data,self.offset)
        self.offset += size
        return values[0] if len(values)==1 else values

    def string(self):
        end = self.data.find(b'\0',self.offset)
        if end < 0:
            raise AnimationFormatError("Unterminated animation name")
        try:
            value = self.data[self.offset:end].decode('ascii')
        except UnicodeError as error:
            raise AnimationFormatError("Non-ASCII animation name") from error
        self.offset = end+1
        return value

    def finish(self):
        if self.offset != len(self.data):
            raise AnimationFormatError("Unexpected animation payload size")


def expand_quaternion(packed, formats, warnings=None):
    def component(value, format_id, bits):
        for shift in range(7):
            count = 1 << shift
            prefix = 256 - (1 << (shift+1))
            if prefix <= format_id < prefix+count:
                separation = 2.0/(count+1)
                base = -1.0+(format_id-prefix+1)*separation
                mask = (1 << (bits-1))-1
                delta = (value & mask)*separation/mask
                return base-delta if value & (1 << (bits-1)) else base+delta
        raise AnimationFormatError("Invalid compressed quaternion format: " + str(format_id))
    x = component(packed >> 21,formats[0],11)
    y = component(packed >> 10,formats[1],11)
    z = component(packed,formats[2],10)
    squared = x*x+y*y+z*z
    if squared > 1.002:
        raise AnimationFormatError("Compressed quaternion is outside the unit sphere: {:.8f}".format(squared))
    if squared > 1.0:
        if warnings is not None and not warnings:
            warnings.append("Normalized compressed quaternion rounding near a 180-degree rotation")
        return normalize((0.0,x,y,z))
    return (math.sqrt(max(0.0,1.0-squared)),x,y,z)


def load(path):
    return decode(Path(path).read_bytes())


def decode(data):
    roots = blocks(data)
    if len(roots)!=1 or roots[0].children is None:
        raise AnimationFormatError("Expected one animation IFF form")
    root = roots[0]
    if root.tag not in ('CKAT','KFAT') or len(root.children)!=1:
        raise AnimationFormatError("Expected CKAT or KFAT animation")
    version = root.children[0]
    if (root.tag,version.tag) not in (('CKAT','0001'),('KFAT','0003')):
        raise AnimationFormatError("Unsupported animation version: " + root.tag+'/'+version.tag)
    if version.children is None:
        raise AnimationFormatError("Expected animation version form")
    compressed = root.tag=='CKAT'
    warnings=[]
    integer = 'h' if compressed else 'i'
    items = {}
    for block in version.children:
        if block.tag in items:
            raise AnimationFormatError("Duplicate animation section: " + block.tag)
        items[block.tag] = block
    unknown = set(items)-{'INFO','XFRM','AROT','SROT','ATRN','STRN','MSGS','LOCT','LOCR','QCHN'}
    if unknown:
        raise AnimationFormatError("Unsupported animation sections: " + ', '.join(sorted(unknown)))

    def required(tag):
        if tag not in items:
            raise AnimationFormatError("Missing animation section: " + tag)
        return items[tag]

    info = Reader(required('INFO').payload)
    fps = info.read('f')
    last_frame, transform_count, rotation_count, static_rotation_count, translation_count, static_translation_count = info.read(integer*6)
    info.finish()
    if not math.isfinite(fps) or fps <= 0 or min(last_frame,transform_count,rotation_count,static_rotation_count,translation_count,static_translation_count)<0:
        raise AnimationFormatError("Invalid animation header")

    def scalar_channel(block):
        if block.tag!='CHNL':
            raise AnimationFormatError("Expected translation channel")
        reader = Reader(block.payload)
        keys = [(reader.read(integer),reader.read('f')) for _ in range(reader.read(integer))]
        reader.finish()
        return Channel(keys)

    def rotation_channel(block):
        if block.tag!='QCHN':
            raise AnimationFormatError("Expected quaternion channel")
        reader = Reader(block.payload)
        count = reader.read(integer)
        formats = reader.read('3B') if compressed else None
        keys=[]
        for _ in range(count):
            frame = reader.read(integer)
            rotation = expand_quaternion(reader.read('I'),formats,warnings) if compressed else reader.read('4f')
            keys.append((frame,rotation))
        reader.finish()
        return Channel(keys,True)

    for tag in ('AROT','ATRN'):
        if tag in items and items[tag].children is None:
            raise AnimationFormatError("Expected channel form: "+tag)
    rotations = [rotation_channel(b) for b in items['AROT'].children] if 'AROT' in items else []
    translations = [scalar_channel(b) for b in items['ATRN'].children] if 'ATRN' in items else []
    static_rotations, static_translations = [], []
    if 'SROT' in items:
        reader=Reader(items['SROT'].payload)
        for _ in range(static_rotation_count):
            if compressed:
                formats=reader.read('3B')
                rotation=expand_quaternion(reader.read('I'),formats,warnings)
            else:
                rotation=reader.read('4f')
            static_rotations.append(Channel([(0,rotation)],True))
        reader.finish()
    if 'STRN' in items:
        reader=Reader(items['STRN'].payload)
        static_translations=[Channel([(0,reader.read('f'))]) for _ in range(static_translation_count)]
        reader.finish()
    if tuple(map(len,(rotations,static_rotations,translations,static_translations))) != (
            rotation_count,static_rotation_count,translation_count,static_translation_count):
        raise AnimationFormatError("Animation channel counts disagree with INFO")
    tracks={}
    transform_blocks=required('XFRM').children
    if transform_blocks is None or len(transform_blocks)!=transform_count:
        raise AnimationFormatError("Animation transform count disagrees with INFO")
    for block in transform_blocks:
        if block.tag!='XFIN':
            raise AnimationFormatError("Expected XFIN transform record")
        reader=Reader(block.payload)
        name=reader.string().lower()
        animated=reader.read('b')
        rotation_index=reader.read(integer)
        mask=reader.read('B' if compressed else 'I')
        indices=reader.read(integer*3)
        reader.finish()
        if not name or name in tracks or mask & ~0x38 or animated not in (0,1):
            raise AnimationFormatError("Invalid or duplicate animation transform: " + name)
        try:
            if rotation_index < 0 or min(indices)<0:
                raise IndexError()
            rotation=(rotations if animated else static_rotations)[rotation_index]
            channels=tuple((translations if mask & (8 << axis) else static_translations)[index]
                           for axis,index in enumerate(indices))
        except IndexError as error:
            raise AnimationFormatError("Animation channel index is out of range: " + name) from error
        tracks[name]=Track(rotation,channels)
    clip=Clip(fps,last_frame,tracks,source_format=root.tag+'/'+version.tag,warnings=warnings)
    if 'MSGS' in items:
        children=items['MSGS'].children
        if not children or children[0].tag!='INFO':
            raise AnimationFormatError("Invalid animation message form")
        reader=Reader(children[0].payload)
        count=reader.read('h'); reader.finish()
        if count!=len(children)-1:
            raise AnimationFormatError("Animation message count mismatch")
        for block in children[1:]:
            if block.tag!='MESG':
                raise AnimationFormatError("Expected animation message")
            reader=Reader(block.payload)
            count=reader.read('h'); name=reader.string()
            frames=[reader.read('h') for _ in range(count)]
            reader.finish()
            clip.messages.append({'name':name,'frames':frames})
    if 'LOCT' in items:
        reader=Reader(items['LOCT'].payload)
        clip.average_speed=reader.read('f')
        clip.locomotion_translation=Channel([(reader.read('h'),reader.read('3f')) for _ in range(reader.read('h'))])
        reader.finish()
    if compressed and 'QCHN' in items:
        clip.locomotion_rotation=rotation_channel(items['QCHN'])
    elif not compressed and 'LOCR' in items:
        reader=Reader(items['LOCR'].payload)
        clip.locomotion_rotation=Channel([(reader.read('h'),reader.read('4f')) for _ in range(reader.read('h'))],True)
        reader.finish()
    return clip


def chunk(tag,payload):
    return tag.encode('ascii')+struct.pack('>I',len(payload))+payload


def form(tag,*children):
    return chunk('FORM',tag.encode('ascii')+b''.join(children))


def transform_name_key(name):
    """Client CrcString ordering: unsigned SWG CRC first, then ASCII name.

    KeyframeSkeletalAnimationTemplate::getTransformIndex binary-searches XFRM
    in this order. Crc.cpp uses non-reflected CRC-32/BZIP2, not zlib.crc32.
    """
    name=name.lower()
    crc=0xffffffff
    for value in name.encode('ascii'):
        crc ^= value << 24
        for _ in range(8):
            crc=((crc << 1) ^ (0x04c11db7 if crc & 0x80000000 else 0)) & 0xffffffff
    return crc ^ 0xffffffff,name


def _encode(clip):
    if not clip.tracks or not math.isfinite(clip.fps) or clip.fps<=0 or clip.last_frame<0:
        raise AnimationFormatError("Cannot export an empty or invalid animation")
    rotations,static_rotations,translations,static_translations,transforms=[],[],[],[],[]
    pack=lambda fmt,*values:struct.pack('<'+fmt,*values)

    def string(value):
        if not value or '\0' in value:
            raise AnimationFormatError("Invalid animation name")
        return value.encode('ascii')+b'\0'

    def channel_record(channel,quaternion=False):
        rows=channel.keys
        if any(frame!=int(frame) or frame>clip.last_frame for frame,_ in rows):
            raise AnimationFormatError("Export keys must use integer frames within the clip")
        if quaternion:
            values=[]
            for frame,value in rows:
                value=normalize(value)
                if values and sum(a*b for a,b in zip(values[-1][1],value))<0:
                    value=tuple(-v for v in value)
                values.append((int(frame),value))
            constant=all(max(abs(a-b) for a,b in zip(values[0][1],value))<1e-7 for _,value in values)
            if constant:
                index=len(static_rotations); static_rotations.append(pack('4f',*values[0][1]))
            else:
                index=len(rotations)
                rotations.append(chunk('QCHN',pack('i',len(values))+b''.join(pack('i4f',frame,*value) for frame,value in values)))
        else:
            constant=all(abs(rows[0][1]-value)<1e-7 for _,value in rows)
            if constant:
                index=len(static_translations); static_translations.append(pack('f',rows[0][1]))
            else:
                index=len(translations)
                translations.append(chunk('CHNL',pack('i',len(rows))+b''.join(pack('if',int(frame),value) for frame,value in rows)))
        return not constant,index

    named_tracks=[(name.lower(),track) for name,track in clip.tracks.items()]
    if len({name for name,_ in named_tracks})!=len(named_tracks):
        raise AnimationFormatError('Duplicate animation transform names after lowercasing')
    # The runtime does not sort this table on load. Unsorted records can decode
    # correctly here yet silently leave most game joints in their bind pose.
    for name,track in sorted(named_tracks,key=lambda row:transform_name_key(row[0])):
        animated,rotation_index=channel_record(track.rotation,True)
        mask,indices=0,[]
        for axis,channel in enumerate(track.translations):
            moving,index=channel_record(channel)
            indices.append(index)
            if moving:mask |= 8 << axis
        transforms.append(chunk('XFIN',string(name)+pack('biI3i',int(animated),rotation_index,mask,*indices)))
    children=[chunk('INFO',pack('f6i',clip.fps,clip.last_frame,len(transforms),len(rotations),len(static_rotations),len(translations),len(static_translations))),
              form('XFRM',*transforms),form('AROT',*rotations),chunk('SROT',b''.join(static_rotations)),
              form('ATRN',*translations),chunk('STRN',b''.join(static_translations))]
    if clip.messages:
        messages=[chunk('INFO',pack('h',len(clip.messages)))]
        for message in clip.messages:
            frames=message['frames']
            if any(frame<0 or frame>clip.last_frame for frame in frames):
                raise AnimationFormatError("Animation message lies outside the export range")
            messages.append(chunk('MESG',pack('h',len(frames))+string(message['name'])+pack('h'*len(frames),*frames)))
        children.append(form('MSGS',*messages))
    for tag,channel,fmt in (('LOCT',clip.locomotion_translation,'3f'),('LOCR',clip.locomotion_rotation,'4f')):
        if channel:
            if any(frame!=int(frame) or not 0<=frame<=min(clip.last_frame,32767) for frame,_ in channel.keys):
                raise AnimationFormatError("Locomotion frames exceed the format's signed 16-bit range")
            payload=(pack('f',clip.average_speed) if tag=='LOCT' else b'')+pack('h',len(channel.keys))
            payload+=b''.join(pack('h'+fmt,int(frame),*(normalize(value) if tag=='LOCR' else value)) for frame,value in channel.keys)
            children.append(chunk(tag,payload))
    return form('KFAT',form('0003',*children))


def encode(clip):
    try:
        return _encode(clip)
    except (struct.error,UnicodeError,OverflowError) as error:
        raise AnimationFormatError("Animation values exceed the file format's limits") from error


def save(path,clip):
    # Encode completely before touching the chosen destination.
    Path(path).write_bytes(encode(clip))
