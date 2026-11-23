import { Module } from '@nestjs/common';
import { DatabaseSync } from 'node:sqlite';
import { ApiController } from './api.controller';
import { openDb } from './db';
import { Runner } from './runner.service';

@Module({
  controllers: [ApiController],
  providers: [{ provide: DatabaseSync, useFactory: () => openDb() }, Runner],
})
export class AppModule {}
