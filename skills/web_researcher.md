---
name: web-researcher
description: 深度网络调研与信息收集
triggers: [搜索, 查询, 调研, 查资料, 最新, 新闻, 资讯, 信息, 资料]
tools: [web_search, web_fetch, knowledge_search, knowledge_add]
---

# 深度网络调研

- 主题不明确时,先用 web_search 多角度搜索。
- 搜索结果不足或需原文时,用 web_fetch 抓取网页正文。
- 信息收集后:
  1. 归纳核心观点
  2. 标注信息来源
  3. 指出不确定之处
- 重要发现可用 knowledge_add 存入知识库备后续检索。
- 汇报时给出结构化摘要。
