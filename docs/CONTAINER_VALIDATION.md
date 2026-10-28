# 本地容器交付实测

2026-10-27，补齐此前只有配置的Docker交付验证。此次使用官方文档语料、Hashing/BM25及抽取式回答，不读取模型凭据或本地E5权重。

## 复现

本机Docker Linux引擎就绪后，在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.verify_container --output evaluation/reports/container-validation-new.json
```

脚本构建镜像，创建`rag-verify-<uuid>`专属Compose项目、数据卷及随机`127.0.0.1`端口。报告路径必须不存在。可用`--existing-image <本机镜像ID>`跳过构建，此时报告明确记录该参数，不计入构建通过。

验证后清理本次项目的容器和数据卷，不影响普通`docker compose up`创建的演示数据卷。镜像、独立运行目录与日志留在本地。验证器拒绝SSH/TCP远程Docker endpoint，始终绑定检测到的本机endpoint。

## 最终实测结果

[原始报告](../evaluation/reports/container-validation-release-rerun-20261027.json)的13项检查全部通过：本机endpoint、Linux引擎、镜像构建、回环端口、只读根文件系统、UID/GID 10001、健康状态、容器无API_KEY环境项、容器依赖一致性、HTTP业务验证、重启后回环端口、重启后索引修订一致、专属资源清理。

12次请求并发3，逐次检查指定版本的引用、缺少版本的澄清、未收录版本的拒答，以及HTTP编号与trace关联。它们是三类重复业务请求，不是12道答案质量评测。日志、实际依赖快照、生成Compose和构建日志保存在报告的`run_directory`，并记录文件SHA256；实际镜像ID在`container.image_id`。

重启可能改变Docker分配的临时宿主端口。首次验证器没有重新读取端口，导致重启检查失败，保留[首轮报告](../evaluation/reports/container-validation-20261027.json)。修复后[首次重跑](../evaluation/reports/container-validation-20261027-rerun.json)通过，增加本机endpoint保护后再次从构建开始得到最终通过报告。失败记录未覆盖。

最终补充日志读取超时仍清理的保护，并显式声明PyYAML开发依赖；本机已安装6.0.3。该版本首次复跑时Docker引擎未运行，保留`container-validation-release-20261027.json`失败记录；启动已安装引擎后，从构建开始复跑得到上述最终通过报告。

20项相关工程测试通过，JUnit保留本地`work/container-validation-release-tests.xml`。新测试覆盖重启端口变化、凭据／公开端口／HTTP失败后的清理和失败报告保留、远程endpoint在联系前被拒绝，以及日志采集超时仍清理。本轮没有重跑此前166项完整工程测试和E5测量。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_container_verification.py tests/test_delivery.py -q --basetemp=work/pytest-container-new --junitxml=work/container-tests-new.xml
```

面试可表述：实现并实测非root、只读文件系统的本地RAG容器交付，以独立卷和回环端口验证业务状态、引用、重启与清理，保存实际镜像和依赖证据。当前未验证E5容器路径、真实生成延迟、生产容量或远端CI；本次增量没有改变开发题人工审核状态。
