import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { NestExpressApplication } from '@nestjs/platform-express';
import { existsSync } from 'node:fs';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create<NestExpressApplication>(AppModule);
  // In Docker the built Vue app is served from the same port as the API.
  const uiDir = process.env.UI_DIR ?? '../ui/dist';
  if (existsSync(uiDir)) app.useStaticAssets(uiDir);
  const port = Number(process.env.PORT ?? 3000);
  await app.listen(port);
  console.log(`Evals platform on http://localhost:${port}`);
}
bootstrap();
