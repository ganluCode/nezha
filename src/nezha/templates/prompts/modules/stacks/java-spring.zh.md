### 项目规范 — JAVA / SPRING BOOT

实现任何代码前，先阅读这些文件了解项目：
- `pom.xml` 或 `build.gradle` — 构建工具、依赖、Java 版本
- `src/main/resources/application.yml`（或 `.properties`）— 配置
- 现有的 `*Controller.java`、`*Service.java`、`*Repository.java` — 命名和包规范

**典型 Spring Boot 结构**：
```
src/
  main/
    java/<base-package>/
      controller/     <- REST controllers (@RestController)
      service/        <- 业务逻辑 (@Service)
      repository/     <- JPA repositories (extends JpaRepository)
      entity/         <- JPA entities (@Entity)
      dto/            <- 请求/响应 DTOs
      config/         <- Spring 配置 (@Configuration)
      exception/      <- 自定义异常 + @ControllerAdvice
    resources/
      application.yml
  test/
    java/<base-package>/
      controller/     <- @WebMvcTest 或 @SpringBootTest
      service/        <- @ExtendWith(MockitoExtension.class)
```

测试命令：`mvn test` 或 `./gradlew test`

**Spring 特定测试层次**：
- Service 层：Mockito 单元测试（`@ExtendWith(MockitoExtension.class)`）
- Controller 层：`@WebMvcTest` + `MockMvc`
- 集成测试：`@SpringBootTest` 仅用于关键流程

### SPRING 最佳实践

- **分层**：Controller → Service → Repository。绝不跳层。
- **Controller 不写业务逻辑** — 仅校验输入并委托
- **不直接暴露 entity** — API 响应必须用 DTO
- **异常处理**：Service 抛自定义异常，`@ControllerAdvice` 统一捕获
- **输入校验**：在 controller 用 Bean Validation（`@Valid`、`@NotBlank` 等）
- **事务管理**：在 service 层用 `@Transactional`
- **分页**：列表接口用 `Pageable`
- **日志**：用 SLF4J（`private static final Logger log = LoggerFactory.getLogger(...)`)
- **绝不在生产代码中用 `System.out.println`**
