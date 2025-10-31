Model/Function
Input Type
Output Type
Executable Tasks

Image Format Converter
Image (any format compatible with ComfyUI)
Image/Tensor/PIL Image (converted format)
1. Convert image to tensor for model input2. Convert tensor to image for visualization3. Convert between PIL Image and tensor for advanced operations
Image Batch Processor
Batch of images
Single image/List of images
1. Split image batch into individual images2. Combine multiple images into a batch3. Process images in batch for efficient handling
Image Resizer/Cropper
Image (any format)

Resized/Cropped image
1. Resize image with various modes (keep ratio, fixed size)2. Crop image by coordinates or ratio3. Pad image with specified color and size
Image Channel Handler
Image (RGB/grayscale)
Single channel image/Multi-channel image
1. Extract single channel (R/G/B/grayscale) from image2. Combine multiple single channel images into multi-channel image3. Convert image to grayscale/invert colors
Image Metadata Manager
Image with metadata/Image without metadata
Image with updated metadata/Extracted metadata
1. Add custom metadata (e.g., processing params, description) to image2. Extract saved metadata from image3. Load/save images with metadata handling
Video Frame Processor
Video file/Image sequence
Processed image sequence/Video file
1. Extract frames from video2. Combine image sequence into video3. Perform frame interpolation, reversal, or selection for video editing
Image Loader
File path (image file)
Loaded image (compatible with ComfyUI)
Load image from local file system or specified path
Video Loader
File path (video file)
Loaded video frames/Video object
Load video from local file system, extract frames or load as video object
Image Saver
Image (any format compatible with ComfyUI)
Saved image file (local path)
Save image to local file system with specified format (e.g., PNG, JPG)
Video Saver
Image sequence/Video frames
Saved video file (local path)
Combine image sequence into video, save video to local file system with specified codec
Checkpoint Loader
Model file path (ckpt/safetensors)
Loaded model (unet, vae, clip etc.)
Load diffusion model checkpoints, including unet, vae, clip components
VAE Loader
VAE file path (ckpt/safetensors)
Loaded VAE model
Load Variational Autoencoder models for image encoding/decoding
Clip Loader
Clip model file path
Loaded Clip model
Load Clip models for text encoding in text-to-image pipelines
Unet Loader
Unet model file path (ckpt/safetensors)
Loaded Unet model
Load Unet (U-Net) models, the core component for diffusion process in image generation
Diffusion Sampler
Loaded model (Unet), sampler settings
Sampled image batch

Perform diffusion sampling (e.g., Euler, DPM++, LMS) to generate images from noise
VAE Encoder
Image (RGB/grayscale)
Latent tensor
Encode input image into latent space using VAE (Variational Autoencoder)
VAE Decoder
Latent tensor
Decoded image (RGB)
Decode latent tensor back into visible image space using VAE
CLIP Text Encoder
Text prompt(s), CLIP model
Text embedding tensor
Encode text prompts into numerical embeddings using a loaded CLIP text model, which are used to guide image generation in diffusion pipelines
omnigen2

Text input (bilingual Chinese-English, max 512 characters)
Generate high-fidelity images (up to 16 scene variations per generation, suitable for marketing/design scenarios)
Multi-style Generation: Support creation of images in 30+ scene types like realistic, fantasy, retro, and compatible with AI design workflow integration

sd1.5
Image upload (JPG/PNG, max 8MB, resolution ≥512×512) + text prompts (bilingual Chinese-English, max 200 characters) + model checkpoint selection
Generate diverse images (up to 10 style variations per generation, compatible with Stable Diffusion ecosystem tools)
Style Customization: Enable image generation with thousands of community-trained checkpoints, supporting fantasy, anime, photorealistic and other styles, and compatible with ControlNet for pose/line control
Sd xl

Image upload (JPG/PNG, max 12MB, resolution ≥1024×1024) + text prompts (bilingual Chinese-English, max 256 characters) + style preset selection
Generate high-detail images (up to 12 style variations per generation, compatible with SDXL-oriented plugins and workflows)
Ultra-realistic & Diverse Style Generation: Support creation of images with enhanced detail and coherence in styles like hyperrealism, cyberpunk, watercolor, and integrate with advanced control tools for finer element manipulation

qwen_image_edit

Image upload (JPG/PNG, max 8MB, resolution ≥768×768) + text instructions (bilingual Chinese-English, max 128 characters)
Generate refined image edits (supporting multiple revision iterations, compatible with mainstream image editing workflows)
Precise Image Editing: Enable targeted modifications like object addition/removal, style adjustment, and background replacement with high coherence to the original image

Image-to-3D
One or more 2D images (user-uploaded photos, design sketches, architectural floor plans, etc.)
3D model in mesh format (not an image type)
1. Object Generation: Generate 3D object models of specific categories from images2. Scene Generation: Generate 3D scenes with multiple objects from multiple images or layout/line drawing images3. Model Detail Restoration and Optimization: Restore 3D shape/structural details and optimize model realism/quality
KColors-Image-to-Image
Image upload (JPG/PNG, max 10MB, resolution ≥1024×1024) + text instructions (bilingual Chinese-English, max 256 characters)
High-quality images (up to 9 style variations per generation, compatible with Photoshop/Figma)
Style Transfer: Transform images into 20+ preset artistic styles or custom style reference image fusion (only style conversion)
FluxContext
Image (JPG/JPEG/PNG, ≤4.7MB, resolution ≤4096×4096, aspect ratio ≤3) + text instructions (English/proper nouns, clear editing needs)
High-quality edited images (resolution single-side [512,1536], aspect ratio consistent with original)
1. Local Image Modification: Replace specific objects, adjust color/texture details2. Style Transfer: Convert artistic styles (e.g., realistic→cartoon)3. Scene Reconstruction: Add/remove background elements, adjust composition4. Text Addition: Add watermarks/text at specified positions5. Semantic-level Editing: Complex operations via natural language (e.g., "replace cat with Shiba Inu + rainbow background")
Character-Feature-Preservation
Reference Image (JPG/JPEG/PNG/JFIF, ≤5MB, resolution ≤4096×4096, must contain people/animals/objects) + text description
High-quality images (preserve subject appearance and facial features from reference image)
1. Subject Appearance Preservation: Generate images consistent with reference subject's appearance (clothing/shape)2. Facial Feature Preservation: Retain character facial details (proportions/expressions) + integrate text needs3. Multimodal Fusion: Balance reference subject features with user creative needs (character design/advertisement scenes)
Contour-Control
Image files (JPG/JPEG/PNG/BMP, <5MB, resolution <4096×4096, width/height 512-1024px, aspect ratio 1:3 to 3:1)
High-aesthetic images (ByteDance 2.0 model, preserve input image contours)
Controllable Image-to-Image: Generate new images with consistent contours (contours = scribbles)
Depth-Control
Image files (JPG/JPEG/PNG/BMP, <5MB, resolution <4096×4096, width/height 512-1024px, aspect ratio 1:3 to 3:1)
High-aesthetic images (ByteDance 2.0 model, preserve input image depth of field)
Controllable Image-to-Image: Generate new images with consistent depth of field (depth of field = depth)
Human-Pose-Control
Image files (JPG/JPEG/PNG/BMP, <5MB, resolution <4096×4096, width/height 512-1024px, aspect ratio 1:3 to 3:1)
High-aesthetic images (ByteDance 2.0 model, preserve input human pose features)
Controllable Image-to-Image: Generate new images with consistent human actions (poses = pose)
Smart-Image-Expansion
Image (original image to be expanded) + optional prompt (text describing expansion direction/style)
Expanded high-definition image
Pixel Expansion: Expand pixels left/right/up/down based on image center (e.g., add 128 pixels left/right)
Subject-Preservation
Image files (JPG/JPEG/PNG/BMP, ≤5MB, resolution ≤4096×4096)
New scene images (preserve subject, background replaced via prompts)
1. Subject Segmentation and Preservation: Automatically identify/segment subjects, preserve details2. Background Replacement: Seamlessly integrate subjects into new backgrounds via prompt instructions
Flux-Model-Text-to-Image
Text description (natural language defining generation needs)
High-resolution images
1. Complex Scene Synthesis: Generate multi-element interaction scenes (e.g., "dinosaur + mechanical city")2. Style Transfer and Mixing: 100+ styles, support intensity adjustment/multi-style overlay3. Dynamic Element Control: Adjust lighting/perspective/color via prompt parameters4. Surreal Creation: Generate fantasy creatures/alien landscapes with visual logic
2x-Image-Super-Resolution
Low-resolution images (JPG/PNG, single/batch upload)
High-resolution images (2x magnification, maintain original ratio, 5-10s per image)
Clarity Enhancement: Preserve content, reduce blur/aliasing, enhance sharpness/texture
4x-Image-Super-Resolution
Low-resolution images (JPG/PNG, single/batch upload)
High-resolution images (4x magnification, maintain original ratio, 5-10s per image)
Clarity Enhancement: Preserve content, reduce blur/aliasing, enhance sharpness/texture
2x-Video-Super-Resolution
Low-resolution videos (MP4/AVI/MOV, resolution ≤1080p)
High-resolution videos (2x magnification, maintain frame rate/ratio, 1-5 mins per video)
Quality Enhancement: Preserve content, reduce blur/aliasing/compression artifacts, enhance sharpness/texture
2x-Video-Frame-Rate-Enhancement
Low frame rate videos (MP4/AVI/MOV, frame rate ≤30fps)
2x high frame rate videos (e.g., 30fps→60fps, maintain resolution/duration, 2-10 mins per video)
Smoothness Enhancement: Increase video frame rate
3x-Video-Frame-Rate-Enhancement
Low frame rate videos (MP4/AVI/MOV, frame rate ≤30fps)
3x high frame rate videos (e.g., 30fps→90fps, maintain resolution/duration, 2-10 mins per video)
Smoothness Enhancement: Increase video frame rate
ltxv-Text-to-Video-2s
Text description (bilingual Chinese-English, max 256 characters)
2-second video
1. Dynamic Scene Generation: Convert text to coherent videos, support complex actions2. Stylized Videos: 20+ artistic styles, control color/lighting/atmosphere3. Physics Simulation: Generate physical dynamic effects (liquid/smoke) and simple special effects
ltxv-Image-to-Video-2s
Base Image (JPG/PNG, resolution ≥512px, static images/keyframe sequences) + text prompt (bilingual Chinese-English, describe dynamic changes)
2-second video
1. Static to Dynamic: Convert single images to coherent actions (e.g., "make figures dance")2. Complex Motion Simulation: Support fluid/smoke/cloth dynamics
ltxv-Text-to-Video-3s
Text description (bilingual Chinese-English, max 256 characters)
3-second video
1. Dynamic Scene Generation: Convert text to coherent videos, support complex actions2. Stylized Videos: 20+ artistic styles, control color/lighting/atmosphere3. Physics Simulation: Generate physical dynamic effects (liquid/smoke) and simple special effects
ltxv-Image-to-Video-3s
Base Image (JPG/PNG, resolution ≥512px, static images/keyframe sequences) + text prompt (bilingual Chinese-English, describe dynamic changes)
3-second video
1. Static to Dynamic: Convert single images to coherent actions (e.g., "make figures dance")2. Complex Motion Simulation: Support fluid/smoke/cloth dynamics
ltxv-Video-to-Video

Video material + optional brief description (define generation needs)
Video
Video Editing: Modify source video content to generate new video content
Wan2.1-T2V
Text description (Chinese-English, defining video generation needs)
High-quality video
1. Dynamic Scene Generation: Convert text to coherent videos, support complex actions2. Stylized Videos: 20+ artistic styles, control color/lighting/atmosphere3. Physics Simulation: Generate physical dynamic effects (liquid/smoke) and simple special effects
Wan2.1-I2V
Image input (any standard size JPG/PNG) + text prompt (Chinese-English, describing dynamic changes)
Dynamic video
1. Static to Dynamic: Convert single images to coherent actions (e.g., "make figures dance")2. Complex Motion Simulation: Support fluid/smoke/cloth dynamics
Wan2.2-Fun Control
Reference video + fun control instructions (e.g., action style, special effect type, etc.)
Creative video
1. Action Style Customization: Adjust the style of character actions (e.g., exaggerated, cartoonish actions)2. Special Effect Addition: Add fun special effects (e.g., particles, deformation effects) to the video
Wan2.2-Text-to-Video
Text description (Chinese-English, defining video content, style, dynamics, etc. in detail)
High-quality video
1. Multi-element Scene Generation: Create video scenes with complex interactions (e.g., "Astronauts planting plants on Mars")2. Style Mixing: Support the superposition and intensity adjustment of multiple artistic styles
Wan2.2-Image-to-Video
Reference image (JPG/PNG, including characters/scenes, etc.) + text prompt (Chinese-English, describing dynamic changes)
Dynamic video
1. Static Image Dynamization: Make elements in the image produce coherent actions (e.g., "Let the bird in the painting fly")2. Scene Dynamic Expansion: Extend a dynamic scene based on the image