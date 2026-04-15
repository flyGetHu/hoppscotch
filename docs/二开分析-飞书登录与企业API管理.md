# Hoppscotch 二开可行性分析：飞书登录 + 企业内部 API 管理

> 分析日期：2026-04-15
> 项目版本：基于当前 main 分支

---

## 一、项目架构总览

| 层 | 技术栈 | 包名 | 说明 |
|---|--------|------|------|
| **前端** | Vue 3 + Vite + TypeScript | `hoppscotch-selfhost-web` | 主应用（自托管 Web 版） |
| **后端** | NestJS + GraphQL + Prisma + PostgreSQL | `hoppscotch-backend` | API 服务端 |
| **公共组件/逻辑** | Vue 3 Composables / Services | `hoppscotch-common` | 前端共享层（组件、服务、页面） |
| **管理后台** | 独立 Vue 应用 | `hoppscotch-sh-admin` | Admin Panel（用户/团队/配置管理） |
| **桌面端** | Tauri (Rust) | `hoppscotch-desktop` | 桌面客户端 |
| **数据层** | Prisma ORM | `hoppscotch-backend/prisma/schema.prisma` | PostgreSQL |
| **内核** | Vite 构建 | `hoppscotch-kernel` | 共享运行时逻辑 |
| **其他** | - | `hoppscotch-cli`, `hoppscotch-js-sandbox`, `hoppscotch-relay`, `hoppscotch-agent` 等 | CLI / 沙箱 / 中继 / AI Agent |

### 关键目录结构

```
packages/
├── hoppscotch-backend/
│   ├── prisma/schema.prisma          # 数据库模型定义
│   └── src/auth/                      # 认证系统核心
│       ├── strategies/                # Passport Strategy 实现
│       ├── guards/                    # 路由守卫
│       ├── auth.module.ts             # 动态 Provider 注册
│       ├── auth.controller.ts         # 认证路由
│       └── helper.ts                  # AuthProvider 枚举 & 工具函数
├── hoppscotch-selfhost-web/
│   └── src/platform/auth/web/index.ts # 前端认证平台实现
├── hoppscotch-common/
│   ├── src/components/firebase/Login.vue  # 登录 UI 组件
│   └── src/pages/                        # 页面路由
└── hoppscotch-sh-admin/              # 管理后台（独立应用）
    └── src/pages/teams/              # 团队管理页面
```

---

## 二、飞书登录接入可行性：高，完全可行

### 2.1 现有 OAuth 架构

项目已实现 **3 个完整的 SSO Provider**，架构模式高度统一且可扩展：

| Provider | Strategy 文件 | Guard 文件 | Passport 库 | 协议 |
|----------|--------------|------------|-------------|------|
| Google | `google.strategy.ts` | `google-sso.guard.ts` | `passport-google-oauth20` | OAuth 2.0 |
| GitHub | `github.strategy.ts` | `github-sso.guard.ts` | `passport-github2` | OAuth 2.0 |
| Microsoft | `microsoft.strategy.ts` | `microsoft-sso.guard.ts` | (自定义/azure-ad) | OIDC |

#### 核心设计优势

**1) Provider 动态注册机制**

文件：`packages/hoppscotch-backend/src/auth/auth.module.ts`

```typescript
// 启动时从数据库 infra_config 表读取启用的 providers
static async register() {
  const allowedAuthProviders = await getConfiguredSSOProvidersFromInfraConfig();

  const providers = [
    ...(authProviderCheck(AuthProvider.GOOGLE, allowedAuthProviders)
      ? [GoogleStrategy] : []),
    ...(authProviderCheck(AuthProvider.GITHUB, allowedAuthProviders)
      ? [GithubStrategy] : []),
    ...(authProviderCheck(AuthProvider.MICROSOFT, allowedAuthProviders)
      ? [MicrosoftStrategy] : []),
    // ← 新 Provider 在此追加即可
  ];
}
```

添加新 Provider **不需要修改核心注册逻辑**，只需在数组中追加条件判断。

**2) 通用 Account 数据模型**

文件：`packages/hoppscotch-backend/prisma/schema.prisma:120-132`

```prisma
model Account {
  id                   String   @id @default(cuid())
  userId               String
  provider             String          // "google" / "github" / "microsoft" → 可扩展为 "feishu"
  providerAccountId    String          // 第三方用户唯一 ID
  providerRefreshToken String?
  providerAccessToken  String?
  providerScope        String?
  loggedIn             DateTime @default(now()) @db.Timestamptz(3)
  user                 User     @relation(...)
  @@unique([provider, providerAccountId])
}
```

飞书可直接复用此表，`provider` 字段填 `"feishu"` 即可。

**3) 配置驱动 — 所有参数存数据库**

所有 OAuth 参数通过 `infra_config` 表管理，支持加密存储：

| 配置项 | 说明 | 加密 |
|--------|------|------|
| `{PROVIDER}_CLIENT_ID` | 应用 Client ID | 是 |
| `{PROVIDER}_CLIENT_SECRET` | 应用 Client Secret | 是 |
| `{PROVIDER}_CALLBACK_URL` | 回调地址 | 否 |
| `{PROVIDER}_SCOPE` | 授权范围 | 否 |

可在 Admin 后台可视化配置，无需改代码重启。

**4) 前端 Login 组件可插拔**

文件：`packages/hoppscotch-common/src/components/firebase/Login.vue:359-398`

```typescript
const authProvidersAvailable: AuthProviderItem[] = [
  { id: "GITHUB",        icon: IconGithub,     label: "...", action: signInWithGithub, ... },
  { id: "GITHUB:ENTERPRISE", icon: IconGithub, label: "...", action: signInWithGithub, ... },
  { id: "GOOGLE",         icon: IconGoogle,     label: "...", action: signInWithGoogle, ... },
  { id: "MICROSOFT",      icon: IconMicrosoft,  label: "...", action: signInWithMicrosoft, ... },
  { id: "EMAIL",          icon: IconEmail,      label: "...", action: () => { mode = 'email' }, ... },
  // ← 追加飞书按钮即可
]
```

组件根据后端返回的 `allowedAuthProviders` 列表动态渲染启用的按钮。

**5) 认证流程统一**

所有 SSO Provider 遵循相同流程：
```
前端点击登录 → 重定向到 /auth/{provider} → 第三方授权 → 回调到 /auth/{provider}/callback
→ validate() 查找/创建用户 → 生成 JWT Token → 写 Cookie → 重定向回前端
```

### 2.2 接入飞书需要改动的内容

#### 新建文件（3 个）

| 文件路径 | 参考模板 | 说明 |
|----------|---------|------|
| `backend/src/auth/strategies/feishu.strategy.ts` | `google.strategy.ts` | Feishu Passport Strategy。推荐使用 `passport-feishu-oauth2` 或参考 Microsoft 的 OIDC 实现手动调用飞书 Open API |
| `backend/src/auth/guards/feishu-sso.guard.ts` | `google-sso.guard.ts` | Feishu SSO 路由守卫 |
| `common/src/assets/icons/auth/feishu.svg` | 现有图标风格 | 飞书登录按钮图标 |

#### 修改文件（7 个）

| 文件路径 | 改动内容 | 改动量 |
|----------|---------|--------|
| `backend/src/auth/helper.ts:25-30` | `AuthProvider` 枚举加 `FEISHU = 'FEISHU'` | ~1 行 |
| `backend/src/types/InfraConfig.ts` | 加 `FEISHU_CLIENT_ID`, `FEISHU_CLIENT_SECRET`, `FEISHU_CALLBACK_URL`, `FEISHU_SCOPE`, `FEISHU_APP_ID`, `FEISHU_APP_SECRET` | ~6 行 |
| `backend/src/infra-config/helper.ts:45-82` | `getAuthProviderRequiredKeys()` 加 Feishu 的配置键映射 | ~6 行 |
| `backend/src/auth/auth.module.ts:49-59` | `register()` 加 Feishu Strategy 条件注册 | ~3 行 |
| `backend/src/auth/auth.controller.ts` | 加 `GET /auth/feishu` 和 `GET /auth/feishu/callback` 路由（参考 google 的两个路由） | ~25 行 |
| `common/src/components/firebase/Login.vue` | `authProvidersAvailable` 数组加飞书按钮项；加 `signInUserWithFeishu()` 方法 | ~15 行 |
| `selfhost-web/src/platform/auth/web/index.ts` | 加 `signInUserWithFeishu()` 方法（重定向到 `/auth/feishu`） | ~5 行 |

#### 总计：约 **10 个文件**，预估改动量 **~100-150 行代码**

### 2.3 飞书接入注意事项

1. **协议选择**：飞书支持标准 **OIDC（OpenID Connect）**，项目已有 Microsoft（OIDC）实现可作最佳参考模板
2. **用户标识**：飞书企业版可能使用 `open_id` 而非 email 作为主标识，需适配 `findUserByEmail()` → 可能需要改为按 `open_id` 查询或同时支持两种方式
3. **Profile 字段映射**：飞书返回的 user profile 结构与 Google 不同，需适配 `createUserSSO()` 和 `updateUserDetails()` 中的字段提取逻辑
4. **Admin 配置界面**：需要在 Admin Dashboard 设置页增加飞书配置表单（或初期直接操作 `infra_config` 数据库表）
5. **回调 URL 自愈**：项目已有 callback URL 自动修正逻辑（`buildDerivedEnv()`），飞书的回调 URL 也应加入

---

## 三、企业内部 API 管理可行性：原生支持，开箱即用

### 3.1 已有的团队协作体系

#### 数据模型（Prisma Schema）

```prisma
// 团队
model Team {
  id              String            @id @default(cuid())
  name            String
  TeamCollection  TeamCollection[]  // 团队 API 集合（文件夹树）
  TeamEnvironment TeamEnvironment[] // 团队共享环境变量
  TeamInvitation  TeamInvitation[]  // 待处理邀请
  members         TeamMember[]      // 团队成员
  TeamRequest     TeamRequest[]     // 团队请求记录
}

// 成员 + RBAC 角色
model TeamMember {
  id      String         @id @default(uuid())
  role    TeamAccessRole // OWNER | EDITOR | VIEWER
  userUid String
  teamID  String
}

enum TeamAccessRole {
  OWNER   // 完全控制
  EDITOR  // 编辑 API 集合/请求
  VIEWER  // 只读查看
}

// 团队 API 集合（文件夹）
model TeamCollection {
  id         String           @id @default(cuid())
  parentID   String?          // 支持嵌套文件夹
  teamID     String
  title      String
  orderIndex Int
  data       Json?            // 集合元数据
  requests   TeamRequest[]    // 包含的 API 请求
}

// 团队 API 请求
model TeamRequest {
  collectionID String
  teamID       String
  title        String
  request      Json             // 完整请求定义（REST/GQL）
  mockExamples Json?            // Mock 示例
  orderIndex   Int
}

// 团队环境变量
model TeamEnvironment {
  id        String @id @default(cuid())
  teamID    String
  name      String
  variables Json               // 环境变量键值对
}

// 团队邀请
model TeamInvitation {
  id           String         @id @default(cuid())
  teamID       String
  creatorUid   String
  inviteeEmail String
  inviteeRole  TeamAccessRole // 邀请时指定角色
}
```

### 3.2 RBAC 权限矩阵

| 操作 | OWNER | EDITOR | VIEWER |
|------|-------|--------|--------|
| 查看团队 API 集合 | ✅ | ✅ | ✅ |
| 查看 API 请求详情 | ✅ | ✅ | ✅ |
| 创建/编辑 API 请求 | ✅ | ✅ | ❌ |
| 删除 API 请求 | ✅ | ✅ | ❌ |
| 创建/编辑集合文件夹 | ✅ | ✅ | ❌ |
| 管理环境变量 | ✅ | ✅ | ❌ |
| 邀请成员 | ✅ | ❌ | ❌ |
| 移除成员 / 修改角色 | ✅ | ❌ | ❌ |
| 删除团队 | ✅ | ❌ | ❌ |
| 团队设置 | ✅ | ❌ | ❌ |

### 3.3 Admin 管理后台功能

`hoppscotch-sh-admin` 包含完整的管理功能（42 个相关文件）：

**团队管理**
- `pages/teams/index.vue` — 团队列表
- `pages/teams/_id.vue` — 团队详情与设置
- `components/teams/Members.vue` — 成员列表与管理
- `components/teams/Invite.vue` — 邀请新成员
- `components/teams/Details.vue` — 团队信息编辑
- `components/teams/PendingInvites.vue` — 待处理邀请
- `components/teams/Add.vue` — 创建新团队
- GraphQL Mutations: `CreateTeam`, `RenameTeam`, `RemoveTeam`, `AddUserToTeamByAdmin`, `RemoveUserFromTeamByAdmin`, `ChangeUserRoleInTeamByAdmin`, `TeamInvitationAdded`, `TeamInvitationRemoved`, `RevokeTeamInvitation`, `AcceptTeamInvitation`
- GraphQL Queries: `TeamListV2`, `TeamInfo`, `TeamsOfUserByAdmin`, `Metrics`, `PendingInvites`

**用户管理**
- `pages/users/index.vue` — 用户列表
- `pages/users/_id.vue` — 用户详情
- `pages/users/invited.vue` — 已邀请用户
- `components/users/Teams.vue` — 用户所属团队
- `components/users/Details.vue` — 用户详细信息
- `components/users/InviteModal.vue` — 邀请用户弹窗
- GraphQL Queries/Mutations: `InvitedUsers`

**系统配置**
- `InfraConfig` 表驱动配置（OAuth、邮件、安全等）
- JWT Secret / Session Secret 自动生成与加密存储
- 速率限制配置
- 分析数据收集开关

### 3.4 企业场景能力清单

| 能力 | 状态 | 说明 |
|------|------|------|
| 团队创建与管理 | ✅ 原生支持 | Admin 和普通用户均可创建 |
| API Collection 共享 | ✅ 原生支持 | REST + GraphQL，支持嵌套文件夹 |
| 角色权限控制 (RBAC) | ✅ 原生支持 | Owner / Editor / Viewer 三级 |
| 成员邀请 | ✅ 原生支持 | 邮件邀请 + 邀请码 |
| 团队环境变量 | ✅ 原生支持 | 团队级共享密钥/配置 |
| API Mock Server | ✅ 原生支持 | 团队级 Mock，含日志和活动历史 |
| API 文档发布 | ✅ 原生支持 | Published Docs，支持自动同步 |
| Personal Access Token | ✅ 原生支持 | API / CLI 访问令牌 |
| 请求历史记录 | ✅ 原生支持 | UserHistory，含星标功能 |
| 导入/导出 | ✅ 原生支持 | Postman / Insomnia / OpenAPI / cURL |
| Admin 全局管理 | ✅ 原生支持 | 独立 Admin Panel |
| 短链接分享 | ✅ 原生支持 | Shortcode |
| WebSocket 实时协作 | ✅ 原生支持 | 基于 Socket.IO |

---

## 四、潜在挑战与风险

| 风险点 | 严重程度 | 详细说明 | 应对建议 |
|--------|---------|---------|---------|
| 无通用 OIDC 抽象层 | 中 | 每个 Provider 是独立 Strategy，没有统一的 OIDC wrapper | 复制 Microsoft Strategy 改造，工作量小 |
| 飞书 Profile 字段差异 | 低 | 飞书返回的用户信息结构与 Google/GitHub 不同 | 适配 `validate()` 中的字段提取逻辑 |
| 邮箱 vs Open ID 匹配 | 低 | 当前用 `findUserByEmail()` 匹配用户，飞书企业版可能无公开邮箱 | 增加 open_id 查询路径或要求飞书应用授权 email scope |
| 前端图标资源 | 低 | 需要飞书 SVG 图标 | 从飞书官方获取品牌图标放入 `assets/icons/auth/` |
| Admin 配置 UI 缺失 | 中 | Admin 后台暂无飞书配置表单 | 初期直接写 `infra_config` 数据库表；后续加 Admin 页面 |
| Callback URL 自愈 | 低 | `buildDerivedEnv()` 未包含飞书 | 在 `callbackConfigs` 数组中追加飞书条目 |
| 飞书网络环境 | 低 | 企业内网可能需要代理访问飞书 API | 在 Strategy 中配置代理选项 |

---

## 五、实施建议

### 开发顺序

```
阶段 1: 环境准备
├── 确保项目可正常运行（pnpm dev）
├── 准备飞书开放平台应用（获取 App ID / App Secret）
├── 配置回调 URL: {VITE_BACKEND_API_URL}/auth/feishu/callback
└── 准备 PostgreSQL 数据库

阶段 2: 飞书后端接入 (~4 个文件)
├── 新建 feishu.strategy.ts（参考 microsoft.strategy.ts 的 OIDC 方式）
├── 新建 feishu-sso.guard.ts
├── 修改 auth.module.ts / auth.controller.ts / helper.ts
├── 扩展 InfraConfigEnum 和 getAuthProviderRequiredKeys()
└── 测试 /auth/feishu → 飞书授权 → 回调 → 创建用户全流程

阶段 3: 飞书前端接入 (~3 个文件)
├── Login.vue 加飞书按钮和 signInUserWithFeishu()
├── platform/auth/web/index.ts 加跳转方法
├── 添加飞书 SVG 图标
└── 端到端测试登录流程

阶段 4: 企业定制化
├── 利用现有 Team 功能组织内部 API
├── 通过 Admin 管理用户和团队
├── 配置团队环境变量（开发/测试/生产）
├── 按需定制 RBAC 策略
└── 配置 Mock Server 用于前后端联调
```

### 工作量估算

| 任务 | 预估复杂度 |
|------|-----------|
| 飞书 OAuth 后端 Strategy | 中（参考现有模板） |
| 飞书前端登录按钮 | 低 |
| 配置项扩展（InfraConfig） | 低 |
| Admin 配置界面（可选） | 中 |
| 企业团队初始化与配置 | 低（利用现有功能） |
| **总计** | **约 2-3 天（含测试）** |

---

## 六、结论

| 维度 | 评估 | 详情 |
|------|------|------|
| **飞书登录** | 高度可行 | 已有 3 个成熟 OAuth 模板，插件式架构，约改 10 个文件 |
| **企业 API 管理** | 原生支持 | 内置 Team + RBAC + Collection 共享 + Admin 后台，开箱即用 |
| **整体二开友好度** | 优秀 | Monorepo 清晰、Schema 合理、配置驱动、前后端分离 |
| **代码质量** | 良好 | TypeScript 全覆盖、Prisma ORM、GraphQL API、fp-ts 函数式风格 |
| **文档完善度** | 中等 | 有 .env.example、schema 清晰，但部分模块缺少独立文档 |
