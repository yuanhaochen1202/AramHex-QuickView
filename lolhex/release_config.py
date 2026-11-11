"""定制程序的固定发布源与签名公钥；数据库更新不使用本配置。"""

# 维护版不访问此源。仅允许用户版获取经本机发布密钥签名的文件。
UPDATE_BASE_URLS = (
    "https://aramhex-updates-1512332557.cos.ap-guangzhou.myqcloud.com",
)
SIGNING_PUBLIC_KEY_HEX = "e8e5a19fd4d9fa56cb9570bea3605eb4d995a7778c5f4ddbaba3948e08c3c7e1"
