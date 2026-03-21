### PROJECT CONVENTIONS — JAVA / SPRING BOOT

Before implementing anything, read these files to understand the project:
- `pom.xml` or `build.gradle` — build tool, dependencies, Java version
- `src/main/resources/application.yml` (or `.properties`) — configuration
- Any existing `*Controller.java`, `*Service.java`, `*Repository.java` — naming and package conventions

**Typical Spring Boot structure**:
```
src/
  main/
    java/<base-package>/
      controller/     <- REST controllers (@RestController)
      service/        <- Business logic (@Service)
      repository/     <- JPA repositories (extends JpaRepository)
      entity/         <- JPA entities (@Entity)
      dto/            <- Request/Response DTOs
      config/         <- Spring configuration (@Configuration)
      exception/      <- Custom exceptions + @ControllerAdvice
    resources/
      application.yml
  test/
    java/<base-package>/
      controller/     <- @WebMvcTest or @SpringBootTest
      service/        <- @ExtendWith(MockitoExtension.class)
```

Test command: `mvn test` or `./gradlew test`

**Spring-specific test layers**:
- Service layer: Mockito unit tests (`@ExtendWith(MockitoExtension.class)`)
- Controller layer: `@WebMvcTest` with `MockMvc`
- Integration: `@SpringBootTest` only for critical flows

### SPRING BEST PRACTICES

- **Layering**: Controller → Service → Repository. Never skip layers.
- **No business logic in controllers** — controllers only validate input and delegate
- **No direct entity exposure** — always use DTOs in API responses
- **Exception handling**: throw custom exceptions in Service, catch in `@ControllerAdvice`
- **Input validation**: use Bean Validation (`@Valid`, `@NotBlank`, etc.) in controllers
- **Transaction management**: use `@Transactional` in service layer
- **Pagination**: use `Pageable` for list endpoints
- **Logging**: use SLF4J (`private static final Logger log = LoggerFactory.getLogger(...)`)
- **Never use `System.out.println`** in production code
