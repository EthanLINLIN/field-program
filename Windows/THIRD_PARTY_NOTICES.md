# 第三方文件与许可核查

核查日期：2026-11-17。公开发布前仍需解决下列未确认事项；此文件不代替许可证原文。

## 模型

- **YuNet**：`face_detection_yunet_2023mar.onnx` 的 SHA-256 为 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`，与 [OpenCV 上游文件](https://huggingface.co/opencv/opencv_zoo/blob/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx)一致。[模型目录](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)声明 MIT，原始[许可证](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE)及版权声明已收录在 `LICENSES/YuNet-MIT.txt`。
- **FERPlus int8**：`emotion-ferplus-12-int8.onnx` 的 SHA-256 为 `3e47195d79e9593294df9e81a6d296a1e10969b68a717284081c29493a0ff5f1`，与 [ONNX Model Zoo 发布的文件](https://huggingface.co/onnxmodelzoo/emotion-ferplus-12-int8/commit/26e8ef3e99684a3928558f78f7a8f4d17b26b0de)一致。本仓库不包含或再分发该权重，程序首次运行时由使用者从上游下载并校验，缓存于仓库目录之外。但[模型说明](https://github.com/onnx/models/blob/main/validated/vision/body_analysis/emotion_ferplus/README.md)写 MIT，[模型仓库页面](https://huggingface.co/onnxmodelzoo/emotion-ferplus-12-int8/tree/26e8ef3e99684a3928558f78f7a8f4d17b26b0de)元数据标为 Apache-2.0。两者均为宽松许可，但当前无法确认究竟如何适用于该权重；下载和使用并不会消除许可不确定性；使用者应自行审阅上游条件，具体许可及再分发要求仍建议向维护者确认。训练数据的使用条件也应独立核查，不能直接推断为权重许可。

## 视频及图片

`display/videos` 中三个视频、`shared_assets/ui_reference/opening_ui_reference_02.png` 和 `display/assets/guide/opening_ui_reference_04_prompt_clean.png`：项目权利人 Yichen Lin 确认本人制作并拥有发布权，并将这些自有素材与项目自有代码一同按根目录 MIT 许可证发布。视频副本去除了部分容器元数据。

## 软件依赖和项目许可

依赖通过 `requirements.txt` 在本机安装，并非随仓库打包其运行环境；实际依赖和传递依赖各自适用各自许可证。Python、Flask、NumPy、OpenCV、ONNX Runtime、pyserial、pywin32 和可选 PlatformIO 的许可证、版权及二进制分发条款需要在打包依赖时单独核查。

项目自有代码及上述五项自有素材采用根目录的 MIT 许可证，版权主体为 Yichen Lin。该许可证不取代第三方模型和依赖各自的许可证。
