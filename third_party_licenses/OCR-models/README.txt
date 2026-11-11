OCR 模型来源与许可（核对日期：2026-11-07）

本发行包随 RapidOCR 3.9.2 使用以下三个未修改的 ONNX 模型：
  PP-OCRv6_det_small.onnx
  PP-OCRv6_rec_small.onnx
  ch_ppocr_mobile_v2.0_cls_mobile.onnx

模型权重版权归百度及 PaddleOCR 相关权利人；RapidOCR 负责转换和打包。
这些模型按 Apache-2.0 分发，完整许可副本见 PaddleOCR-LICENSE。
本定制发行版没有重新训练、转换或改写模型权重，也不代表原权利人背书。

核查依据：RapidAI 官方模型许可页列明这三个制品及各自 SHA-256，
实际打包文件哈希全部相符。MODELS.json 记录名称、哈希、源地址。
该官方声明说明目前这三个模型没有单独的 NOTICE；后续替换模型时
需要重新核对相应许可、来源、版权声明和哈希，不能沿用本次结论。

RapidOCR 模型许可与归属声明：
https://rapidai.github.io/RapidOCRDocs/latest/model_licenses/

PaddleOCR 项目许可证：
https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE

PaddlePaddle 官方 PP-OCRv6 ONNX 模型卡（均标注 Apache-2.0）：
https://huggingface.co/PaddlePaddle/PP-OCRv6_small_det_onnx
https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec_onnx

分类模型官方归档：
https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version2.x/ppocr/model_list.en.md

随包的 RapidOCR 3.9.2 模型解析配置：
_internal/rapidocr/default_models.yaml

RapidOCR 转换后的 ONNX 与 PaddlePaddle 官方 ONNX 不保证逐字节相同；
本包校验的是 RapidOCR 官方声明与其 v3.9.2 发布记录所列的实际制品。
