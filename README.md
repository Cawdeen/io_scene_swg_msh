# io_scene_swg
A Blender add-on for importing and exporting various Star Wars Galaxies asset formats.

### Blender Version Support
Should work with Blender 2.9+ and 3+

### Features
- Import / export formats:
  - mgn (Animated Mesh)
  - msh (Static Mesh)
  - lod (Level of Detail Static Mesh Group)
  - pob (Portalized Object)
  - skt (Skeleton)
- Generate .apt, .sat and .lmg files
- Generate materials from .sht (shader) files
- Generate .pob hierarchies and interior buildouts

### Recommended External Tools
- [Sytner's Iff Editor (SIE)](https://modthegalaxy.com/index.php?resources/sie.1/)
  - Tool used to edit almost all of SWG's file types.
  - Requires forum account to download.
- [Krita](https://krita.org/en/)
  - Free and open source image editing software. Very handy replacement for Photoshop.
- [NVIDIA Texture Tools Exporter](https://developer.nvidia.com/texture-tools-exporter)
  - Standalone app recommended. Useful for converting image types like PNG into DDS textures useable by SWG.
  - Included in this repository is `swg_dds_nvidia_tex_tools_preset.dpf`, a preset for the Texture Tools that works with SWG's DDS support.
  - Can accurately display DDS files and will show where errors in your textures will occur (typically caused by alpha transparency in PNGs).
  - Requires signing in with a NVIDIA Account. The tools are free, but can be a bit of a hassle to download.
- Krita DDS Python Plugin [Import](https://github.com/esuriddick/Programming/tree/main/Python/Krita/DDS_File_Importer) / [Export](https://github.com/esuriddick/Programming/tree/main/Python/Krita/DDS_File_Exporter)
  - An alternative to the NVIDIA Texture Tools. It's a bit clunky and doesn't have a way to show you if you screwed something up before you get into the game, but it works.

## How To Use

### Skeletons and animation controls

Open the 3D View sidebar (`N`) > **SWG** > **SWG Animation**.

1. **Import SWG Skeleton** imports native game joints. By default only LOD 0 is loaded; disable **LOD 0 Only** to import all skeleton LODs. Lower LODs start hidden in the viewport.
2. Optionally enable **Add FK Control Rig** during import, or select the imported armature in Object Mode and click **Create FK Control Rig** later.
3. Select the control armature, enter Pose Mode and rotate its named bones. Root translation is available; child translation and scale controls are locked. The rig currently provides FK controls; ANS import/export is available below. IK remains future work; FBX/Mixamo retargeting is available below.
4. **Export Rest Skeleton** exports the game skeleton's collection, including its LODs, even when the control rig is selected. It exports rest data, not the current animation pose, and excludes the control armature.

The readable controls meet at rest and live in a separate armature. They remain parented without Blender's physical bone-connection flag, allowing SWG child-joint translation keys to work. Hidden output bones preserve each native joint's orientation while world-space constraints drive the game skeleton. For skinning, use the game skeleton as the mesh's Armature modifier target, not the controls. Existing MGN vertex groups can retain their original weights; this step does not automatically bind meshes or replace weights.

Native skeletons use the same full `(-x, -z, y)` coordinate conversion as MGN's default Z-forward/Y-up import (including the MGN reader's Z flip). A local X-axis reflection keeps bone frames right-handed. SKT joint pre/post rotations, bind rotations/translations, rotation orders, parent relationships and LOD identifiers are retained. Bone display length does not change the joint rest frames. The old **Connect Bones** import option is replaced by separate FK controls because pointing a game bone toward its child changes its animation axes.

Reimport skeletons created by the previous importer to use the new control rig. Previously imported skeletons retain the legacy export path; new native skeletons use the corrected rest-transform export. Do not mix legacy and native skeletons in a single export collection. A rig must be created before animation or constraints are added to the game joints.

### SWG animation import and export

Select the native SWG skeleton, its FK rig, or a mesh bound to it. In **SWG Animation**, use **Import Animation** / **Export Animation**; both are also available through File > Import/Export > SWG Animation (.ans). Reimport legacy skeletons with the current SKT importer first.

**Import:** supports compressed `CKAT/0001` and uncompressed `KFAT/0003`. It creates a new Blender Action and retains the previous Action. The sidebar's Action selector can switch between imported clips. **Use Animation Frame Rate** defaults on and sets the scene FPS and playback range; when disabled, action key times are converted to the current scene FPS. Import bakes samples at the source clip's frame rate with quaternion continuity and linear Blender F-curves. Interframe Blender interpolation can differ slightly from the client's quaternion slerp; exported samples are evaluated at the chosen output FPS.

On an FK rig, imported actions drive the controls; without a rig, they drive the native game joints. Early connected FK rigs are upgraded without changing rest positions, lengths or parenting so child-joint translation tracks can play. Import stops before replacing the Action if NLA tracks or drivers would interfere. Strict joint matching is optional: otherwise unmatched weapon/attachment joint tracks are retained as custom-property F-curves in **Extra Joint: name** groups and exported, with a warning that the selected skeleton cannot pose them.

**Export:** writes client-readable, uncompressed `KFAT/0003` and bakes the evaluated game skeleton, including FK constraints and NLA pose motion. The active Action's exact frame range and original FPS are defaults; both can be changed in the export dialog. The exporter restores the current frame and does not replace Actions or modify skeleton rest data. Output files are encoded/validated before the destination is opened. Animated scale/shear is unsupported by SWG and is rejected. Animated game-skeleton object transforms must first be baked into root-joint or locomotion channels. NLA clips carrying SWG message markers must be baked to an active Action with correctly placed markers before export.

Animation messages (for example footsteps) appear as Action pose markers. Existing message names, repeated triggers and timing survive round trips; moving or renaming markers edits them. Locomotion translation, rotation and speed are retained as editable custom-property F-curves under **SWG Locomotion**, in Blender axes. **Preview is in place**: these locomotion channels do not move the character through the viewport. They are exported separately from the joint animation to avoid double-applying root motion. A small number of legacy compressed rotations round just outside the quaternion unit sphere; those are normalized and reported on import.

The evaluated-pose exporter accepts motion produced by the FBX retargeting workflow below. Importing an arbitrary FBX Action alone does not perform bone mapping or rest-pose/axis conversion.

Development checks:

- `python -m unittest discover -s tests -v` checks path resolution and the standalone ANS codec.
- `python tests/check_animation_corpus.py <appearance/animation>` reads an existing animation corpus without changing it.
- `blender --background --factory-startup --python-exit-code 1 --python tests/blender_animation_smoke.py -- <dsrc>` verifies real CKAT/KFAT clips against client matrix equations, authored FK/NLA motion, messages, locomotion, frame rates, child translations and on-disk export/import round trips. Other Blender smoke scripts cover skeletons, weighting and asset imports.

### FBX / Mixamo retargeting

Import the FBX normally, then open **SWG Animation > Retarget FBX**:

1. Set **FBX Source** to the imported armature and **SWG Rig** to the SWG FK control rig. **Source Action** can select another compatible Action; leaving it empty uses the FBX armature's active Action.
2. Click **Load Mixamo Preset**. This finds Mixamo names with or without namespace prefixes, fills an editable mapping table, and loads the source Action's frame range. Use the checkbox to disable a row, bone selectors to edit it, and +/- to add or remove rows. The preset maps 34 human controls; SWG ulna and hold joints inherit from their parents. Extra Mixamo middle/pinky fingers have no direct SWG human counterparts.
3. Leave **Reference Pose > Automatic A/T-Pose Correction** selected and click **Calibrate**. It aligns anatomical facing/up, matches rest bone directions and compensates roll without changing either rest skeleton. Root and terminal controls (including toes) retain their rest orientation because their display tails do not define anatomical directions. **Motion Scale = 0** uses the leg-length ratio; positive values override translation scaling. Uniform FBX object scales such as 0.01 are supported.
4. For manual calibration, pose both rigs to the same posture, return to Object Mode, choose **Use Current Reference Poses**, then **Calibrate**. Recalibrate after changing mappings, rest bones, calibration settings or rig placement.
5. Choose **In Place** for fixed horizontal position with hip bounce, or **SWG Locomotion** to retain planar travel in the separate locomotion channels. Optional **Extract Facing Rotation** moves heading changes into locomotion rotation; pelvic sway can require cleanup. Both modes preview in place, consistent with ANS import.
6. Click **Preview Retarget** and press Space to inspect the resulting Action. Preview samples the source and creates an editable Action; it is not a live constraint link. After editing the source, mapping or settings, generate another preview.
7. Click **Bake to New Action** for a retained Action, then **Export Animation**. Existing Actions are kept. The FBX source Action, both rest skeletons, weights and native game-joint FK constraints are preserved.

The frame range and step control sampling. Step 1 is recommended for final exports. The scene's FPS determines playback and is stored on the new Action. Object scales must be positive and uniform; the SWG control/game armature object transforms must match. Target drivers/constraints/NLA tracks and source NLA clips must be removed/muted before single-Action retargeting. Root travel, rotations and target proportions are transferred; animated stretch/scale, IK foot-contact correction and a five-finger-to-three-finger anatomical solve are not generated. Inspect foot contacts and finger poses before shipping an animation.

Every operation prints **[SWG Retarget]** reports to Blender's system console. Calibration lists mappings, inherited joints, scale and missing-bone warnings. Preview/bake reports source-channel resolution, sampled frames, motion detected and a numerical game-joint check at the start/middle/end. **PREVIEW SUCCESS** / **BAKE SUCCESS** means those checks passed, not that artistic cleanup or in-game playback has been validated. Errors stop processing with a reason. Use **Print Last Report** to repeat the summary, or **Toggle System Console** on Windows to view it.

Mappings and calibration are saved with the .blend. A preset can be reused with compatible Actions on the same source skeleton. A new FBX with different bone names/rest pose should be selected as the source and calibrated again. Retarget outputs also use safe default export filenames for names containing FBX separators such as `|`.

Development check: `blender --background --factory-startup --python-exit-code 1 --python tests/blender_retarget_smoke.py -- <dsrc> [source_fixture.blend]` covers A/T correction, differing unit scales, source directions, both calibration modes, root/heading extraction, error handling, source preservation and actual ANS export/import. The optional isolated library fixture tests an existing imported FBX; it is not needed to run the plugin.

### Clothing weights and occlusion seams

In **SWG Animation > Skin Weights**, select the clothing mesh in Object Mode. Set **Reference Body** to `hum_m_body_l0` (the default) and choose the imported game skeleton or its FK rig. Click **Transfer Body Weights**. The tool copies weights from the body and binds the clothing to the game skeleton, preserving existing nonbone groups, mesh positions, face maps and shape keys. It does not use Blender's bone-heat automatic weights or change the reference body.

- Coincident reference vertices copy their bone weights exactly, including original float values and influence counts.
- New interior vertices sample the nearest body triangle and interpolate its weights. Known face-map zones restrict correspondence to the same body region. **Interior Influence Limit** applies only to these interpolated interiors; zero leaves influences unlimited.
- Protected seams default to face-map borders and open mesh edges. **Smooth Reference Edge** projects new or offset seam vertices onto a compatible reference boundary and interpolates its endpoint weights. Original endpoints retain their exact vectors. This accommodates thicker jackets/armor and different seam vertex spacing, but remeshed seam weights are derived rather than identical to a single original vertex.
- **Copy Nearest Vertex Exactly** copies an original seam vertex's vector unchanged, even when the garment is offset. This gives exact reference values but can produce less smooth transitions on a remeshed seam.
- For a specific cuff, collar or other boundary, use **Target Seams > Vertex Group** and choose a separate nonbone mask group (for example `SWG_SeamLock`). Optionally choose a **Reference Seam Group** on the body to restrict matching to the intended loop. In smooth mode, the reference group's selected edges define the curve; isolated vertices and multi-zone junctions fall back to vertex copies.

Reference and clothing must be aligned in their rest positions in world space. Clothing thickness does not require coincident positions; **Max Reference Distance** limits how far correspondence may reach (zero disables the limit). Distant or ambiguous matches stop the operation before weights change. Automatic boundary detection also sees open edges caused by split topology; use an explicit seam group when those should not be protected.

**Validate Seam Weights** checks the target against the currently selected reference/matching settings without changing it. It reports original-vector copies separately from interpolated seam weights and reports positional offset separately. It detects later weight edits but does not freeze seam weights against subsequent painting or normalization. Agreement with this mapping is not a guarantee against clipping: inspect the garment in representative poses, especially bulky armor and closely spaced surfaces.

MGN export excludes nonbone helper groups when a skeleton is bound/recorded and remaps bone indices accordingly. Unbound meshes exclude reserved `SWG_` helper groups. Weighting does not transfer face-map assignments or body-shape blend shapes; those remain separate asset-authoring steps.

### Asset folder settings
In Blender Preferences > Add-ons > NSG SWG Tools, choose a **Folder Layout**:

- **Single Asset Directory (SWG Source):** set **SWG Source Asset Root** to the client directory containing `appearance`, `shader`, and `texture`.
- **DSRC Repository (Restoration):** set **DSRC Repository Root** to the repository itself, for example `D:/r3git/dsrc`. Assets are searched under `sku.0/sys.client/compiled/game`, then `sku.0/sys.shared/compiled/game`, then `sku.0/sys.server/compiled/game`. The first matching virtual asset path wins.
- **Auto:** set **Asset Root** to either of the above directories. A directory containing `sku.0` is treated as DSRC; otherwise it is treated as a single asset directory. If Asset Root is empty, Auto uses your saved SWG Source Asset Root, preserving existing settings.

The three paths are saved independently. Switching between SWG Source and DSRC retains both paths. Save Preferences if Blender's automatic preference saving is disabled.

POB, LOD, MGN and MSH imports share this lookup, including referenced appearances, floors, shaders, palettes and textures. Import browsers open in the corresponding resolved asset directory. The preferences display the search order and flag missing directories; missing dependencies print their searched paths in Blender's console.

### General SWG helper functions
* In the 3D View toolbar (contains menus like "View", "Select", "Object", etc), the very furthest right menu will be "SWG". This is where you can find some helper functions. NOTE: For many of these to work, you need to configure the asset folder settings above
  * "Initialize MGN data (occlusions, bones, blends) from an existing MGN": Browse to an existing MGN file, and sets up the current objects's Occlusion, Bone and Blend Shape data to match. A very quick way to get started making a similar item like a new wearable. Does not actually assign any mesh verts to the Vertex Groups, faces to the Face Maps, or do anything with materials. 
  * "Find and load materials": If you named your materials identically to a shader filename (no preceding dir or extention, just "concertina_a_aa7" for example), and the path is set correctly, the images and some settings should automatically be applied to the materials in Blender
    * You can repeat selecting "Find and load materials" as often as you want (if you change a SWG Shader on disk, or want to change the shader completely), and it should keep any existing material assignments in the scene. 
  * "Add SWG Shader as Material": Similar to the above, but opens a file browser to select a SWG shader. The Shader is converted to a Material and added in a new Material Slot on the mesh. 
  * "Create a SWG .apt for this .msh": Creates a very simple .apt file at the browsed path representing the APT->MSH file chain. The reference inside the APT will always be "mesh/\<currently selected object name\>.msh" so change your object name accordingly. No support for APT->LOD->MSH or any other file chain yet.
  * "Create a SWG .sat and .lmg for this .mgn": Creates .sat and .lmg files at the browsed path representing the SAT->LMG->MGN file chain. The reference inside the LMG will always be "mesh/\<currently selected object name\>.mgn" so change your object name accordingly.
  * "Generate Blend Shapes From Other": Attempts to use the shape key deltas in one mesh to create shape keys in another. Use Ctrl+click to select 2 meshes. The first is the source and the second is the destination. For every shape key in source, this will create a same-named shape key in destination. In addition, it will actually try to update the vertex deltas in destination's shap keys. It does this by finding the closest vertex in source, and applying the same delta it had in this shape key. This works okay, but not amazing.

### MSH (Static Mesh) Import / Export:
* Import and Export SWG .msh file (versions 0004 and 0005)
* Since version 2.0.0, multi-shader MSHs are imported as one Blender mesh with per-face material assignemnt. Materials are created and properly assigned per shader used in the .msh
* UVs: Multiple UV sets are fully supported for import/export per material. When 1 shader uses multiple UV channels, you need to be sure to use the "UVSets" custom property properly:
  * When an SPS is imported, the number of UV channels it used are added as a custom property, "UVSets", on the specific material (not the main object or mesh). 
  * If you are creating a new object, or want to add more UV sets, create a new UV Map in blender, uv map your faces like normal, and make sure the given material for the shader you are working with has a "UVSets" custom property with the correct number of UV sets assigned
* DOT3: Imports the existance (or not) of DOT3 normalmap coordinates (tangents?), but not the tangents themselves since Blender will reclaculate these. Stored in the "DOT3" custom property per material. If you are creating a new object and want DOT3 for any/all shaders, you need to add a "DOT3" custom property to the material(s) with a value of "1"
* Normals: Imported normals are stored in Blenders' split normals. Split normals are exported. 
* Vertex Colors: Imports color0 and color1 into mesh Color Attributes if present. Adds a Custom Property "Color0" and/or "Color1" onto Materials representing SPSs that use vertex colors. The exporter looks for this Custom Property to determine whether to export them since each SPS can have them or not.
* Extents: Automatically compute Extents (box and sphere)
* Collision Extents: Reads CollisionExtents and stores their binary data in a Custom Property so they can be exported. No edit support, but non-destructive 
* Floor: Saves floor file path in custom property, "Floor". You can add/edit this for export.
* Hardpoints: Supports hardpoints as empty "Arrows" objects. The name of the Arrows empty will become the name of the hardpoint at export. To add a hardpoint:
  * Create an empty Arrows object in Object mode:
    * Shift+A -> Empty -> Arrows 
  * Make the new Empty a child of a mesh object:
    * In Object Mode, multi-select the Arrow then the Mesh
    * Ctrl+P -> Object 
* Import option to "Remove Duplicate Verts". Shouldn't be needed in most cases, but will remove verts that are in the same 3D space and merge them. 

### MGN (Animated Mesh) Import / Export:
* Imports base mesh, UV, Shader Name, Bone names, Vertex weights, Blends, occlusion zones, and skeleton name as follows:
  * The mesh is the active imported object.
  * Joint names are imported as vertex groups.
  * Vertex weights are imported and assigned relative to the vertex groups they belong to.
  * Blends are imported as shape keys.
  * Occlusion layer (2 for most wearables) is stored in the custom property, "OCC_LAYER"
  * Skeleton name(s) are imported as a custom properties. The name of the property will be of the form SKTM_n where n is an integer. This allows multiple skeletons to be affected. The value of the property is the path to the skeleton, including the directory, e.g. "appearance/skeleton/all_b.skt".
  * Occlusion Zones (either zones occluded BY this model on lower layers, or zones occluded ON this model) are imported as Integer type Custom Properties on the main Blender object. The name of the Custom Property will be OZN_<zone name> and value will be either 0 or 1:
    * Zones occluded by this model: The value of the Custom Property will be 1, signifying that this model occludes those zones on lower-layered meshes. 0 if not. 
    * Zones occluded ON this model: The OZN_ Custom Property will still be added, and Blender Face Maps will be created to signify which triangles belong to which occlusion zone.  
  * Shader name is imported as a material, in cases where there are multiple shaders, each shader is added as a new material.  Also,  each polygon in the mesh is properly assigned to each material.
  * UVs are imported for each shader, and stored in a single UV file within blender.  Again, the UVs are assigned properly to each Poly and material that gets created.  This allows you to import any and all textures from the SWG shader files into blender, and they will map properly.   Please be aware that SWG UVs are written to the MGN files Upside-Down.  Meaning they have to be flipped upright on import for them to work properly in blender.   
* This plugin will export a single object from blender into the MGN file format for SWG.  Items exported are the mesh, UV, Shader names, Bone names, bone weights, Blends, Occlusions and skeleton name.
  * Each item works the same as has already been described above for the importer.   This exporter will fail if multiple objects are selected for export.
  * The exporter will also flip the UV Upside down (mirror on the Y axis), on export,  so you don't need to manually flip the UV.
* Hardpoints: Not properly supported (need a skeleton to know the relative positioning), but the binary data is preserved (and uneditable) in a custom property, "HPTS"
* Texture Renderers: Not properly supported but the binary data is preserved (and uneditable) in a custom property, "TRTS"

MGN Workflow Notes: 
  * If you create a new original mesh/object, you'll first need to choose a skeleton file that your mesh should use.  From that skeleton file, you'll want to use the bone names in the file for your vertex group names in Blender.  Then you can assign vertex weights as necessary.  When finished, make sure that the skeleton file name is set as a SKTM_n custom property where n is an integer. If there are no SKTM_ custom properties, one will be automatically added for the most common skeleton ("appearance/skeleton/all_b.skt"), which might not be what you want.
  * If you import an existing MGN,  the vertex groups will be named properly from the start.  The skeleton file to be used will also be added as a custom property to the mesh.
  * Occlusion zones, either BY this model or ON this model
    * To denote an occlusion zone used (for either case), add an Integer type Custom Property named "OZN_<zone name>" on the Blender object. For the value, set it to either 1 (occluded/invisible) or 0 (not occluded/visible). The reason for including a non-occluded (visible) zone is because another part of the occlusion system (marking the triangles on THIS object that belong to occlusion zones) use the same zone names.
    * Occlusions zones this model occludes (OZN and ZTO chunks) are exported based on the existence of Custom Properties. 
      * The SWG client has a list of zones that can be made invisible for humanoid type objects.  Most creatures do not use occlusions, and any extra layers of clothing or items are made to fit exactly without clipping.  For humanoids that can use extra layers of clothing and items,  SWG uses occlusions to avoid clipping with lower level layers.  So using a human as an example,  it loads with a default skin as layer 1.  If you make a shirt for the human to wear,  the shirt may occupy layer 2,  and without any occlusions the layer 1 body can clip through the shirt during movement in some circumstances.  To avoid this clipping, you can use occlusions, which will make faces of the layer 1 meshes invisible. A long sleeved shirt will occlude the chest, torso_f, torso_b, L_arm, R_arm, and maybe the formarms, and maybe even the waist zones… 
      * It's important to understand that you are not occluding zones on the object you're working with, but rather that you are occluding zones on lower layers of any other mgns on this humanoid.    
    * Occlusion zones ON this model (OZN, OZC, FOZC, and OITL chunks): You can specify the Occlusion Zones on this model by using Blender's "Face Map" feature. This is similar to a Vertex Group, but with faces. The name of the group must be either an occlusion zone ("chest", "torso_f") OR a colon-separated list of occlusion zones ("chest:torso_f"). Import a model like armor_padded_s01_chest_plate_m_l0.mgn to see an example. 
      * IMPORTANT: It's required that any zone name used in a Face Map has a corresponding OZN_<zone name> Custom Property. SWG uses the same data chunk to store both the zones occluded BY this model and the zones occluded ON this model. If you create a Face Map with a zone that isn't also OZN_ custom property, there will be a nasty error on export. .
  * Blends / Shape keys:
    * Blends are the basic deformations of the base mesh that define how the object deforms along with the “body shape” sliders within the SWG client.  There are 4 main Blends for most clothing:  flat_chest, Skinny, Fat, and Muscle.  Heads for the various species have many more blends that correspond to the sliders you see at character creation. The base mesh, is also the Basis shape key.  Search google,  research, and learn for yourself how to properly use and save shape keys within blender.
  * DOT3:
    * DOT3 (aka tangents, aka normalmap coords, aka per-pixel lighting coords) are optionally exportable since some shaders don't want them. Controlled by the export option, "DOT3" 

Limitations:
* MGN Hardpoints and Texture Renderers are stored as binary data in a Custom Property on import, but there is no edit support for these. The existing data will be rewritten at export.  
* If you export an MGN with Face Maps while in Blender's Edit Mode, the Face Map data is not properly retrieved so Occlusion Triangles are not properly set. This will usually result in ALL of your mesh's triangles belonging to the first defined Occlusion zone which can do funky things like a helmet hiding all the geometry on a species' body. Exporting in Object Mode seems to work more reliably.

### SKT (Skeleton) Import / Export:
- Imports skeleton LOD, joint name, parenting, rotation, and position data and converts it into multiple armature objects.
  - SWG skeletons can have 1 to 4 levels of detail (LOD). Skeletal animation can get pretty expensive, so each LOD is used to reduce joint counts. Each LOD is imported as a separate armature object.
  - When creating a new skeleton from scratch, create a separate collection for them and place all of your skeleton's LOD armatures within it. You can see the expected structure by importing an existing skeleton.
  - LOD order in the skeleton is determined by the armature order in the skeleton collection, highest detail to lowest in descending order.
  - Using LODs is good! Don't eliminate them if it can be helped. They can be essential for performance on some users' machines!
  - Most skeletons will have 3 or 4 LODs, depending upon their complexity.
- SWG uses joints instead of bones. The difference is that joints have no length, only position and rotation, whereas bones have all three.
  - The importer will calculate tail positions based on 3 conditions:
   - If the bone has children, the tail position will be the averaged position of all child head positions. If there is only one child, the bone will be connected.
   - If the bone has no children but it has a parent, the tail will continue along the trajectory of the parent for half of its length.
   - If the bone has no children and no parent, then it will move positively on the Y-axis at a distance of 0.05.
- Rotation data in the skeleton is saved per joint and comprised of Pre-Multiply Rotations (RPRE), Bind Pose Rotations (BPRO), and Post-Multiply Rotations (RPST). These are a carryover from Maya, the modeling program uses to animate the characters in SWG.
  - The exporter is still unable to create recreate these rotations accurately. Consider exporting modified pre-existing skeletons or skeletons intended to reuse existing animations to be broken for now.
- When exporting the skeleton, the the portion of the collection name prior to any `.` will be used, eg: a collection named `weegee.bunchofnumbers` will be exported as `weegee.skt`.
  - Armature names are ignored. A naming convention of `<skeleton name>_skt_l<detail level>` is recommended but not necessary.
  - Only deform bones are exported. This allows for the use of control bones like IK while animating (animations are not yet supported!).

### POB (Portalized Object) Import / Export:
- Portalized Objects are objects that contain interiors that can be traversed by other objects, like buildings or caves or bigger spaceships like the YT-2400. They are composite objects comprised of many different parts and are some of the most complex and interesting objects in the game.
  - POBs are divided into rooms. They always contain one exterior "room", known as "r0", and at least (but not limited to) one interior room.
  - Room 0, the exterior, will also have an LOD associated with it, usually with 4 detail level static meshes.
  - All other rooms only ever have 1 static mesh.
  - Each room is connected to each other by objects known as a "portals". Portals act as windows between rooms, allowing players to view into the next room through them.
  - Each room also contains a "floor" object, dictating where a mobile object may traverse.
    - Floor objects typically have NPC pathfinding nodes referred to as "Cell Waypoints".
  - Some rooms may possess collider objects, which may be simple shapes or meshes. Some rooms contain none, as the floor object suffices for collision. These are often used to block character line of sight.
- Due to the complex hierarchy required of POBs, the SWG Helper Functions Menu contains a function to generate a POB hierarchy for you. It is highly recommended to use this as a starting point for any POB projects.
- Studying NoStyleGuy's [POB Tutorial](https://github.com/nostyleguy/io_scene_swg_msh/wiki/Portalized-Object-(POB)) is a must.

## Tool Credits
- [Nick "NoStyleGuy" Rafalski](https://github.com/nostyleguy)
  - Tool creator
- [Alec "Elour" Harley](https://github.com/AlecH92)
  - Hardpoint and collision assistance
- [Tim "RhapsodyInGeek" Maccabe](https://github.com/RhapsodyInGeek)
  - .skt import / export
- [Vera "sinewavey" Lux](https://github.com/sinewavey)
  - .skt import, .skt rotation transform math
