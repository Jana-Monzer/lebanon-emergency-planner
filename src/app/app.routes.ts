import { Routes } from '@angular/router';
import { App } from './app';
import { guard } from './services/can-activate';

export const routes: Routes = [{ path: '', component: App, canActivate: [guard] }];
