bl_info = {
    "name": "BSC/BON Proprietary Exporter",
    "author": "ChatGPT",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BSC/BON Export",
    "description": "Export selected meshes and armature to proprietary .bsc/.bon binary formats",
    "category": "Import-Export",
}

import os
import struct
import tempfile
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import bpy
import bmesh
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, BoolProperty
from mathutils import Matrix


# -----------------------------
# Data containers
# -----------------------------

@dataclass
class VertexData:
    pos: Tuple[float, float, float]
    normal: Tuple[float, float, float]
    uv: Tuple[float, float]


@dataclass
class ChunkData:
    name: str
    prefix: str
    flags: int
    material_name: str
    texture_name: str
    vertices: List[VertexData] = field(default_factory=list)
    indices: List[int] = field(default_factory=list)
    object_name: str = ""


@dataclass
class BoneData:
    name: str
    parent_index: int
    matrix_local: Matrix


@dataclass
class WeightData:
    chunk_name: str
    vertex_index: int
    bone_name: str
    weight: float


# -----------------------------
# Prefix parsing / flags
# -----------------------------

PREFIX_FLAG_MAP = {
    "COL": 0x01,
    "DMG": 0x02,
    "ENV": 0x04,
    "LOD": 0x08,
}


def parse_prefix_and_flags(object_name: str) -> Tuple[str, int]:
    """Extract prefix from object name and convert it to a byte flag."""
    prefix = ""
    flags = 0x00

    if "_" in object_name:
        prefix = object_name.split("_", 1)[0].upper()
    else:
        prefix = object_name[:3].upper()

    if prefix in PREFIX_FLAG_MAP:
        flags = PREFIX_FLAG_MAP[prefix]

    return prefix, flags


# -----------------------------
# Blender data extraction
# -----------------------------

def _triangulate_object_eval(context: bpy.types.Context, obj: bpy.types.Object) -> bpy.types.Mesh:
    """Create triangulated evaluated mesh data for export."""
    depsgraph = context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh = obj_eval.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()

    return mesh


def collect_scene_data(context: bpy.types.Context):
    selected_meshes = [o for o in context.selected_objects if o.type == 'MESH']
    selected_armatures = [o for o in context.selected_objects if o.type == 'ARMATURE']

    chunks: List[ChunkData] = []
    texture_names: List[str] = []
    bones: List[BoneData] = []
    weights: List[WeightData] = []

    # Build mesh chunks from selected objects (1 chunk per object/sub-mesh by active material set)
    for obj in selected_meshes:
        mesh = _triangulate_object_eval(context, obj)
        uv_layer = mesh.uv_layers.active.data if mesh.uv_layers.active else None

        prefix, flags = parse_prefix_and_flags(obj.name)

        # Resolve primary material/texture for this object (first slot with TEX_IMAGE)
        mat_name = ""
        tex_name = ""
        if obj.material_slots:
            for slot in obj.material_slots:
                mat = slot.material
                if not mat:
                    continue
                if not mat_name:
                    mat_name = mat.name
                if mat.use_nodes and mat.node_tree:
                    for node in mat.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            tex_name = node.image.name
                            break
                if tex_name:
                    break

        if tex_name and tex_name not in texture_names:
            texture_names.append(tex_name)

        chunk = ChunkData(
            name=obj.name,
            prefix=prefix,
            flags=flags,
            material_name=mat_name,
            texture_name=tex_name,
            object_name=obj.name,
        )

        vertex_map: Dict[Tuple[int, int], int] = {}
        for poly in mesh.polygons:
            for loop_idx in poly.loop_indices:
                loop = mesh.loops[loop_idx]
                key = (loop.vertex_index, loop_idx if uv_layer else -1)

                if key not in vertex_map:
                    vert = mesh.vertices[loop.vertex_index]
                    uv = (0.0, 0.0)
                    if uv_layer:
                        uv_val = uv_layer[loop_idx].uv
                        uv = (uv_val.x, uv_val.y)

                    vertex_map[key] = len(chunk.vertices)
                    chunk.vertices.append(
                        VertexData(
                            pos=(vert.co.x, vert.co.y, vert.co.z),
                            normal=(vert.normal.x, vert.normal.y, vert.normal.z),
                            uv=uv,
                        )
                    )

                chunk.indices.append(vertex_map[key])

        chunks.append(chunk)

        # collect weights from vertex groups
        if obj.vertex_groups:
            vg_map = {vg.index: vg.name for vg in obj.vertex_groups}
            for v in mesh.vertices:
                for g in v.groups:
                    bone_name = vg_map.get(g.group, "")
                    if bone_name:
                        weights.append(
                            WeightData(
                                chunk_name=obj.name,
                                vertex_index=v.index,
                                bone_name=bone_name,
                                weight=g.weight,
                            )
                        )

        obj.evaluated_get(context.evaluated_depsgraph_get()).to_mesh_clear()

    # Build armature data (first selected armature)
    if selected_armatures:
        arm_obj = selected_armatures[0]
        bone_index_map: Dict[str, int] = {}

        for i, bone in enumerate(arm_obj.data.bones):
            bone_index_map[bone.name] = i

        for bone in arm_obj.data.bones:
            parent_idx = bone_index_map[bone.parent.name] if bone.parent else -1
            bones.append(BoneData(name=bone.name, parent_index=parent_idx, matrix_local=bone.matrix_local.copy()))

    return chunks, texture_names, bones, weights


# -----------------------------
# Binary writer: .bsc
# -----------------------------

def write_bsc(filepath: str, context: bpy.types.Context):
    chunks, textures, _bones, _weights = collect_scene_data(context)

    chunk_count = len(chunks) & 0xFF
    tex_count = len(textures) & 0xFF
    control_flag = 0x00

    with open(filepath, "wb") as f:
        # Byte 0 @0x00: UInt8 chunk count
        f.write(struct.pack("<B", chunk_count))
        # Byte 1 @0x01: UInt8 texture count
        f.write(struct.pack("<B", tex_count))
        # Byte 2 @0x02: UInt8 control/flag byte
        f.write(struct.pack("<B", control_flag))

        # Offset 0x03+: 12-byte blocks associated with textures/chunks
        # Descriptor now carries per-chunk data offset to improve runtime compatibility.
        descriptor_start = 0x03
        descriptor_size = 12
        data_start = descriptor_start + (len(chunks) * descriptor_size)

        chunk_offsets: List[int] = []
        cursor = data_start
        for chunk in chunks:
            chunk_offsets.append(cursor)
            # chunk payload = vertices(8 floats each) + indices(uint32 each)
            cursor += (len(chunk.vertices) * (8 * 4)) + (len(chunk.indices) * 4)

        for i, chunk in enumerate(chunks):
            tex_idx = textures.index(chunk.texture_name) if chunk.texture_name in textures else 0xFFFF
            vert_count = len(chunk.vertices) & 0xFFFF
            idx_count = len(chunk.indices) & 0xFFFF
            flags = chunk.flags & 0xFFFF
            data_offset = chunk_offsets[i] & 0xFFFFFFFF

            # 12-byte block: UInt16 texture index
            #                UInt16 vertex count
            #                UInt16 index count
            #                UInt16 flags from object prefix
            #                UInt32 absolute file offset to this chunk payload
            f.write(struct.pack("<HHHHI", tex_idx, vert_count, idx_count, flags, data_offset))

        # Placeholder: optional material/texture extended table (if format requires later)
        # for tex_name in textures:
        #     pass

        # Placeholder: write vertex/index data streams.
        # NOTE: if your engine expects a different interleaving or fixed-point format,
        # adjust this section accordingly.
        for chunk in chunks:
            for v in chunk.vertices:
                # Vertex position: 3 x float32
                f.write(struct.pack("<3f", *v.pos))
                # Vertex normal: 3 x float32
                f.write(struct.pack("<3f", *v.normal))
                # Vertex UV: 2 x float32
                f.write(struct.pack("<2f", *v.uv))

            for idx in chunk.indices:
                # Face index stream (placeholder): UInt32 per index
                f.write(struct.pack("<I", idx))


# -----------------------------
# Binary writer: .bon
# -----------------------------

def write_bon(filepath: str, context: bpy.types.Context):
    chunks, _textures, bones, weights = collect_scene_data(context)

    with open(filepath, "wb") as f:
        bone_count = len(bones)
        weight_count = len(weights)
        chunk_count = len(chunks)

        # Header: UInt16 bone count
        f.write(struct.pack("<H", bone_count))
        # Header: UInt16 weight entry count
        f.write(struct.pack("<H", weight_count))
        # Header: UInt16 chunk count related to this skeleton
        f.write(struct.pack("<H", chunk_count))
        # Header: UInt16 reserved
        f.write(struct.pack("<H", 0))

        # Bone table
        for bone in bones:
            encoded_name = bone.name.encode("utf-8")[:31]
            padded = encoded_name + b"\x00" * (32 - len(encoded_name))
            # Bone name fixed field: 32 bytes
            f.write(struct.pack("<32s", padded))
            # Parent index: Int16 (-1 for root)
            f.write(struct.pack("<h", bone.parent_index))
            # Reserved/padding: UInt16
            f.write(struct.pack("<H", 0))

            flat = [bone.matrix_local[row][col] for row in range(4) for col in range(4)]
            # Bone local matrix: 16 x float32
            f.write(struct.pack("<16f", *flat))

        # Weight entries
        for w in weights:
            chunk_idx = next((i for i, c in enumerate(chunks) if c.object_name == w.chunk_name), 0)
            bone_idx = next((i for i, b in enumerate(bones) if b.name == w.bone_name), -1)

            # Chunk index this weight belongs to: UInt16
            f.write(struct.pack("<H", chunk_idx & 0xFFFF))
            # Vertex index inside source mesh: UInt32
            f.write(struct.pack("<I", w.vertex_index))
            # Bone index influencing vertex: Int16
            f.write(struct.pack("<h", bone_idx))
            # Reserved/padding: UInt16
            f.write(struct.pack("<H", 0))
            # Weight value: float32
            f.write(struct.pack("<f", w.weight))

        # Placeholder: if BON format requires inverse bind pose table or animation links,
        # insert additional chunks here.




def _get_first_image_from_selected_meshes(context: bpy.types.Context):
    """Find first image texture in selected mesh materials."""
    for obj in context.selected_objects:
        if obj.type != 'MESH':
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes or not mat.node_tree:
                continue
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    return node.image
    return None


def _image_to_dds_bytes(image: bpy.types.Image) -> bytes:
    """Try to serialize Blender image to DDS bytes; fallback to minimal DDS header blob."""
    # Try native Blender save path first
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.dds', delete=False) as tmp:
            tmp_path = tmp.name

        original_path = image.filepath_raw
        original_format = image.file_format
        image.filepath_raw = tmp_path
        image.file_format = 'DDS'
        image.save()

        with open(tmp_path, 'rb') as f:
            data = f.read()
        if data.startswith(b'DDS '):
            return data
    except Exception:
        pass
    finally:
        try:
            image.filepath_raw = original_path
            image.file_format = original_format
        except Exception:
            pass
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    # Fallback: minimal DDS-like placeholder (magic + zeroed header)
    return b'DDS ' + b'\x00' * 124




def copy_sidecars_from_template(basepath: str, template_basepath: str):
    """Copy sidecar files from a known-good monster as binary templates."""
    dst_base, _ = os.path.splitext(basepath)
    src_base, _ = os.path.splitext(template_basepath)
    for ext in (".ba0", ".bb0", ".bc0", ".bd0", ".bao", ".bbo"):
        src = src_base + ext
        dst = dst_base + ext
        if os.path.exists(src):
            shutil.copyfile(src, dst)

def write_required_sidecars(basepath: str, context: bpy.types.Context):
    """Create required sidecar files used by the game loader.

    Some builds use numeric zero suffixes (.ba0/.bb0/.bc0/.bd0), while others
    use letter-o suffixes for the first two files (.bao/.bbo).
    We create both variants to maximize compatibility.
    """
    base, _ext = os.path.splitext(basepath)
    image = _get_first_image_from_selected_meshes(context)
    dds_blob = _image_to_dds_bytes(image) if image else (b'DDS ' + b'\x00' * 124)

    for ext in (".ba0", ".bb0", ".bc0", ".bd0", ".bao", ".bbo"):
        sidecar = base + ext
        if not os.path.exists(sidecar):
            with open(sidecar, "wb") as f:
                # Sidecar payload written as DDS-masked data (magic 'DDS ' + payload)
                f.write(dds_blob)


# -----------------------------
# Blender UI / Operators
# -----------------------------

class EXPORT_OT_bsc_bon(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.bsc_bon"
    bl_label = "Export BSC/BON"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".bsc"
    filter_glob: StringProperty(default="*.bsc", options={'HIDDEN'})

    export_bon: BoolProperty(
        name="Export .bon",
        description="Also export matching .bon skeleton/weights file",
        default=False,
    )

    preserve_existing_bon: BoolProperty(
        name="Preserve existing .bon",
        description="Do not overwrite .bon if it already exists (recommended for first tests)",
        default=True,
    )

    use_template_sidecars: BoolProperty(
        name="Copy sidecars from template base",
        description="Copy .ba0/.bb0/.bc0/.bd0/.bao/.bbo from a known-good monster base name",
        default=False,
    )

    template_base: StringProperty(
        name="Template base path",
        description="Path to a known-good .bsc/.bon base name (e.g. C:/.../stalker.bsc)",
        default="",
    )

    export_sidecars: BoolProperty(
        name="Create sidecars (.ba0/.bb0/.bc0/.bd0/.bao/.bbo)",
        description="Create sidecar files as placeholders (numeric-zero and letter-o variants)",
        default=True,
    )

    def execute(self, context):
        bsc_path = self.filepath
        bon_path = os.path.splitext(bsc_path)[0] + ".bon"

        write_bsc(bsc_path, context)
        if self.export_bon:
            if self.preserve_existing_bon and os.path.exists(bon_path):
                pass
            else:
                write_bon(bon_path, context)

        if self.use_template_sidecars and self.template_base:
            copy_sidecars_from_template(bsc_path, self.template_base)
        elif self.export_sidecars:
            write_required_sidecars(bsc_path, context)

        self.report({'INFO'}, f"Exported: {bsc_path}" + (f" and {bon_path}" if self.export_bon else ""))
        return {'FINISHED'}


class VIEW3D_PT_bsc_bon_export(bpy.types.Panel):
    bl_label = "BSC/BON Export"
    bl_idname = "VIEW3D_PT_bsc_bon_export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BSC/BON'

    def draw(self, context):
        layout = self.layout
        layout.label(text="Export proprietary .bsc/.bon")
        op = layout.operator(EXPORT_OT_bsc_bon.bl_idname, text="Export BSC/BON")


classes = (
    EXPORT_OT_bsc_bon,
    VIEW3D_PT_bsc_bon_export,
)


def menu_func_export(self, context):
    self.layout.operator(EXPORT_OT_bsc_bon.bl_idname, text="Proprietary BSC/BON (.bsc)")


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
