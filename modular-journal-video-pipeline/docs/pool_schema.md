# Pool Schema

## 命名规则

- 所有字段统一使用 `snake_case`
- 不使用驼峰，不混大小写，不写临时版本号式字段名
- 同类字段使用固定前缀，便于 README、脚本和后续改写保持一致

## 前缀分组

- `paper_`：论文本体信息
- `source_`：来源与抓取轨迹
- `pool_`：入池与去重状态
- `score_`：评分结果与评分元信息
- `content_`：brief / key points / 标题 / 口播内容
- `video_`：是否已进入视频产出及其结果
- `audit_`：最近一次脚本级更新时间与阶段

---

## 字段说明

### `paper_id`
全局池内的稳定论文 ID。当前由 `paper_title_normalized` 派生，用于在长期 pool 中保持稳定标识。

### `paper_title`
论文原始英文标题，用于展示与后续内容生成。

### `paper_title_normalized`
归一化标题，只用于跨批次去重与合并，不用于展示。

### `paper_abstract`
当前保留的最佳摘要文本。优先保留已有内容，后续可由补全过程补齐。

### `paper_publication_date`
论文发布日期，格式 `YYYY-MM-DD`。

### `paper_journal`
期刊或来源名称。

### `paper_doi`
DOI 字符串；若没有则为空字符串。

### `paper_url`
主论文入口 URL。优先 DOI URL，否则回退到可用落地页。

### `paper_authors`
作者列表。

### `paper_first_author`
第一作者展示字段，便于快速引用。

### `source_origin`
当前主来源。现阶段固定为 `openalex`。

### `source_openalex_id`
当前记录保留的 OpenAlex work ID。

### `source_url`
实际抓到的来源落地页 URL。

### `source_raw_ids`
参与合并的原始 OpenAlex work ID 列表。

### `source_openalex_batch_ts`
该论文出现过的 OpenAlex 时间戳批次列表。对应文件名里的时间戳，而不是单独目录名。

### `pool_created_at`
该论文第一次进入全局池的时间戳。

### `pool_updated_at`
该论文最近一次被流程 2 合并更新的时间戳。

### `pool_duplicate_count`
累计命中过多少次重复抽取并被合并。

### `score_journal_ai`
期刊物理学相关度辅助分，范围为 0 到 1。它不是“AI 领域相关度”，而是让 API 评估该期刊与物理学的相关程度；当前定义为多次 API 采样后，去掉最大最小值，再对中间值取平均的聚合结果。

### `score_journal_ai_samples`
期刊物理学相关度辅助分的原始采样列表。

### `score_journal_ai_model`
生成 `score_journal_ai` 时使用的模型名。

### `score_journal_ai_updated_at`
最近一次更新 `score_journal_ai` 的时间戳。

### `score_journal_impact`
期刊 impact / IF 数值分。它与 `score_journal_ai` 语义相近，都是用于判断期刊是否值得进入后续流程；区别在于这里更接近影响因子/影响力数值，而 `score_journal_ai` 是 0 到 1 的物理学相关度辅助校正分。当前同样允许通过多次 API 采样并做 trimmed mean 聚合。

### `score_journal_impact_samples`
期刊 impact / IF 分的原始采样列表。

### `score_journal_impact_model`
生成 `score_journal_impact` 时使用的模型名。

### `score_journal_impact_updated_at`
最近一次更新 `score_journal_impact` 的时间戳。

### `score_candidate_passed`
候选门控结果。当前规则是 `score_journal_ai >= journal_ai_threshold` 或 `score_journal_impact >= journal_impact_threshold` 任一满足即为 `true`。

### `score_candidate_updated_at`
最近一次更新 `score_candidate_passed` 的时间戳。

### `content_brief`
给视频或摘要展示使用的一句话简介。

### `content_brief_updated_at`
最近一次更新 `content_brief` 的时间戳。

### `content_brief_model`
生成 `content_brief` 时使用的模型名。

### `content_key_points`
页面正文要点列表。

### `content_key_points_updated_at`
最近一次更新 `content_key_points` 的时间戳。

### `content_key_points_model`
生成 `content_key_points` 时使用的模型名。

### `content_title_zh`
中文标题。

### `content_title_zh_updated_at`
最近一次更新 `content_title_zh` 的时间戳。

### `content_title_zh_model`
生成 `content_title_zh` 时使用的模型名。

### `content_voice_intro`
口播开场句。

### `content_voice_intro_updated_at`
最近一次更新 `content_voice_intro` 的时间戳。

### `content_voice_intro_model`
生成 `content_voice_intro` 时使用的模型名。

### `content_voice_points`
口播要点列表。当前 v1 里若语音提示词只返回导语、没有单独要点，则回退为 `content_key_points` 的前两条。

### `content_voice_points_updated_at`
最近一次更新 `content_voice_points` 的时间戳。

### `content_voice_points_model`
生成 `content_voice_points` 时使用的模型名。

### `video_done`
该论文是否已经被做成视频。

### `video_done_at`
视频完成时间戳；未完成则为空字符串。

### `video_bvid`
如果已发布到 B 站，可在这里记录 BVID。

### `audit_created_at`
该条记录第一次写入全局池时的审计时间。

### `audit_updated_at`
最近一次被任一自动流程写回时的审计时间。

### `audit_last_stage`
最近一次更新该记录的流程标记，例如 `stage2_pool_ingest`、`stage3_content`。
