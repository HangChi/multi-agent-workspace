# Requirements Checklist: 013 s12 发现修复

| 条目 | 状态 | 说明 |
|---|---|---|
| US 独立可测 | ✅ | US1（scope）、US2（弹层）、US3（nav）各自对应 s12 项 |
| FR 有锚点 | ✅ | FR-001~008 挂 US 场景与 s12 项号 |
| 边界成节 | ✅ | Edge Cases：owner 门不被 relevant 破坏/成员建需求/重拉失败降级 |
| 「不含」有依据 | ✅ | data-model §明确不做三条 |
| SC 可判定 | ✅ | SC-001 15/15（实测二跑一致）/SC-002 五件套 /SC-003 回填 |

## 与既有契约对齐

- [x] 后端零改动（`GET /projects?scope=relevant` 010 已交付）。
- [x] s12 的验收对象不动（只修 `_open_detail` 等待与 B1 定位器）。
- [x] MemberSection 的 owner 门与列表 scope 正交（C1/C2 双向验）。

## Notes

1. **发现②是混合根因**：app 缺刷新回调（真 bug）+ s12 没等拉取
   （脚本时序，011 曾误判成 app bug）——都修，FR-004/005/006。
2. **scope 切 relevant 不加 tab**（R-03）：010 规格要的是徽章区分，
   看板下拉一直是 relevant，表单/页面与它一致最小惊讶。
3. **detail 不改 query**（R-02）：秒开 UX 是刻意设计，回调 6 行搞定。
