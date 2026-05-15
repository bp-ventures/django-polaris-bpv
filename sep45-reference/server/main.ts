import { Application, Router } from "jsr:@oak/oak";
import {
  ChallengeRequest,
  ChallengeResponse,
  getChallenge,
  getToken,
  TokenRequest,
  TokenResponse,
} from "./challenge.ts";

const router = new Router();

router
  .get("/challenge", async (context) => {
    const params = context.request.url.searchParams;
    const request: ChallengeRequest = Object.fromEntries(
      params,
    ) as ChallengeRequest;
    const response: ChallengeResponse = await getChallenge(request);
    context.response.body = response;
  })
  .post("/challenge", async (context) => {
    const body = await context.request.body.json();
    const request: TokenRequest = body;
    const response: TokenResponse = await getToken(request);
    context.response.body = response;
  });

const app = new Application();
app.use(router.routes());
app.use(router.allowedMethods());

app.listen({ port: 80 });
