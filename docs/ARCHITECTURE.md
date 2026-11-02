# 8K Lossless Video Editor contract

Separate GPL-2.0-only local editor. Product name exact **8K Lossless Video Editor**. Port **8770**; do not alter Lens Studio on8766. No Vercel/cloud/remote media. User wants polished timeline, audio, effects, lossless exports and free public GitHub. Every visible action must work. Preserve original uploaded bytes. Structured subprocess argv, owned cancellable workers, atomic new-file publication, bounded8Kmemory. Agents have separate ownership; root integrates/publishes.

## Project JSON, schema_version 1

Project `{schema_version:1,id?:string,name,width,height,fps:string,audio_sample_rate:48000,audio_layout:'mono'|'stereo'|'5.1'|'7.1',background:'#RRGGBB',color_space:'bt709'|'bt2020-pq'|'bt2020-hlg',tracks:Track[],markers:Marker[]}`. fps rational e.g.'24/1'. Max8192/axis,35,651,584pixels,fps1..120,duration<=86400s. Defaults1920x1080/24/bt709; UI adopts firstvideo dimensions/rate until explicitlychanged.

Track `{id,name,kind:'video'|'audio'|'title',muted:boolean,locked:boolean,clips:Clip[]}`. Higher video index on top; title draws text. Video contributes sourceaudio unlessvolume0/muted; audio tracks mix; gaps background/silence. Lock is interactiononly; mute affectsrender.

Clip `{id,media_id?,start,in,duration,speed,reverse,opacity,volume,fade_in,fade_out,x,y,scale,rotation,flip_h,flip_v,crop:{left,right,top,bottom},exposure,contrast,saturation,temperature,blur,sharpen,text,font_size,color,box_color,box_opacity,keyframes:Record<string,{time,value}[]>,lens:Record<string,number>}`. Seconds; duration isoutputduration, sourceconsumption=duration*speed. x/y projectpixel offsetsfromcenteredfit; cropfractions0..0.49. Defaults speed/opacity/volume/scale/contrast/saturation1, othernumericadjustments0, flagsfalse, emptytext/keyframes/lens. Font_size64,colorwhite,boxblack. Empty lens bypasses; nonempty uses inherited StudioRenderer settings. Title omitsmedia_id. Keyframes clip-local linear, keysopacity/x/y/scale/volume. Markers `{id,time,label,color}`. Project duration=max(start+duration). Opacity+audio fades enable crossfades onoverlap. Unsupportedsettings musterror, never silentlyignored. Exacttrim/split/reorder/speed/transform/text/grade/audio/keyframes where implemented.

Media_map keysmedia_id; values private path pluspublicMedia. Public fields `{id,name,kind:'video'|'audio'|'image',width,height,fps,fps_exact,duration,codec,container,pixel_format,bit_depth,audio_streams,transfer,color_primaries,color_space,color_range,bytes,sha256,proxy_url,poster_url,waveform:number[],warnings:string[]}`. Image duration0 meansUI5sdefault. Audio geometry0. Uploaded local regularfiles only; no arbitrarypaths/URLs. Actual decoderavailability determines supportedformats, not allformatsmarketing. Source paths private. Project JSON storesmediaIDs; UI relink support maps missingIDs to replacementimports.

## Engine interface, engine/project_model.py + editor_pipeline.py

`validate_project(project,media_map=None)->normalizeddict`, `project_duration(project)->seconds` inproject_model.

`capabilities()->JSONdict` installeddecoders/encoders,modes,acceleration.
`render_project(project,media_map,output_path,mode='lossless',progress_callback=None,cancel_event=None,preview=False)->resultdict`.
`render_frame(project,media_map,time_s,width,output_path)->{width,height,time,render_ms,...}` savedJPEG.

Modes: `lossless` FFV1/MKV processedRGB16+float32PCM; `nvenc-lossless` actualprobedHEVCNVENC codec-lossless, YUVconversionclearlyreported; `copy` packetcopy onlyeligiblewholeclips/keyframecuts/homogeneousconcat, rejectmix/effects/unsafeedits; `hevc` highqualityMP4 usingprobedNVENC/VT elseCPU; `prores` highqualityMOV (notlossless). Defaultlosslessmaster. NeverlabelVTlossless. Verify independentdecodedRGB16hash against renderedRGB16 forlosslessmaster, fullfiledecode/framecount/raster/audio/timingforall. Native8K finaloutput; preview downscaleexplicit. ReportprojectCFRresampling/mixedHDR/colorspacewarnings. CompositingmayCPU, inheritedlensCUDA/MPS/CPU; don'tclaimallGPU. Lowthreadcounts/boundedmemory. Reverse mustboundedorguarded, notunboundedFFmpegOOM. Lenscachecanbakeviaoldstreamingadapter buttrueRGB16forlosslessmaster. TitletextviaownedUTF8file; neverfilterstringinterpolateusertext/paths. Validateallnumericfilters. File-onlyFFmpegprotocols. Agentengineowns onlyeditor_pipeline.py/project_model.py/test_editor_pipeline.py; inheritedfilesunchangedunlesscoordinated.

## API, server.py on127.0.0.1:8770

GET `/api/status` -> `{app:'8K Lossless Video Editor',version,ready,device,gpu,accelerated,reason,ffmpeg,capabilities}`.
GET `/api/capabilities` -> enginecapabilities.
GET `/api/media` -> Media[]. POST `/api/media` multipartfile -> Media.
GET `/api/media/{id}/proxy|poster|waveform` ->ownedassets, URLsabove.
GET `/api/projects` -> `[{id,name,updated_at,project}]`.
POST `/api/projects` `{project}` -> `{id,project,updated_at}`; atomicUUIDsave.
GET `/api/projects/{id}` sameenvelope.
POST `/api/preview` `{project,time,width:1280}` -> `{url,width,height,time,render_ms}`; image `/api/frames/{uuid}.jpg`.
POST `/api/jobs` `{project,mode,preview:false}` -> Job.
GET `/api/jobs/{id}` -> Job `{id,status:'queued'|'running'|'completed'|'failed'|'cancelled'|'cancelling',progress:0..1,message,error?,output_url?,output_name?,result?}`.
POST `/api/jobs/{id}/cancel` ->Job. GET `/api/jobs/{id}/download` verifiedcompleteonly.
POST `/api/shutdown` gracefulonlythiseditor.

Allmutationsrequire `X-Editor-Client:8k-lossless-editor`. Trustedloopbackhosts/CORS, originalpathsnotserved. Storeimports `data/media/<uuid>/source.ext`+media.json; projectsdata/projects; rendersdata/jobs; allignored. Actualvideo/audio/imageingest/proxy/poster/waveform. One rendereratatime, busy409previews/cache, queue4. Snapshotrunningproject; editscannotmutateit. Ownedworker/sessioncancellationstopsnativechildren. Persiststate/restartinterruptedjobs. AgentAPIowns server.py/media_library.py/test_server.py only.

## Frontend

AgentUIowns app/**. PolisheddarkprofessionalNLE, compacttopbar/projectsave/undo/redo; media bin upload/search; playerpreview/play/timecode/frame-step; inspectortabsVideo/Color/Audio/Title/Lens; zoomablemultitracktimeline/ruler/playhead/trim/split/duplicate/delete/reorder/drag/snapping; trackadd/mute/lock/rename; markers; keyboardshortcuts; project8Kpresets/fps/colorspace; projectJSONsave/load+serverautosave; relinkofflineIDs; exportdrawertruthfulmodes/progress/cancel/download. Actualrenderedframepreview viaAPI, selectedclipproxyplayback labeled, finaltimelinepreview realjob. No fakeclips/decorativewaveforms. Undo/redoallprojectedits; validatedimports; settingsinvalidatepreview. No remoteassets/fonts/analytics/Next/Vercel. React19/Viteexistingdependenciescopied. Root will useReactbestpracticesskill andrealbrowserQA. APIintegrationtypesfollowthisschema; coordinatenecessarychanges.

## Delivery

Rootowns launchers/setup/docs/CI/Git/integration; noagentsGit/push. Synthetic tests2cliptrim/sequence/overlap/title/audio/keyframe/save-load/cancel/decodedlosslesshash/sourcepreserved; realNVIDIA8K andM4runs supplementCI. Publicrepo Teylersf/8k-lossless-video-editor, freeGPL2, nofootage/ROMs/envs/data/out. Honestimplementedfeature/formatmatrix; no allpossibleNLEfeatures/codecslosslessclaim. OneclickMac/Windowssetup/start/stop. Packagedbinarydownloads onlyclaimifactuallybuilt/smoked. NoVerceldeploy.

Project audio sample rates44100/48000/96000, layoutsmono/stereo/5.1/7.1. Default48kstereo; UIadoptsfirstsourceaudio layout/rate ifknown untiluserchanges. Losslessmastersusefloat32PCMpostmix; broadcast/intermediateprofilesPCM24explicitcompatquantization, deliveryAAC/FLAC asreported. Sourcepacketcopyoriginalchannels/rate/packetsexact.
