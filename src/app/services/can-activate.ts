import {
  ActivatedRouteSnapshot,
  CanActivate,
  CanActivateFn,
  Router,
  RouterStateSnapshot,
  UrlTree,
} from '@angular/router';
import { Config } from './config';
import { inject } from '@angular/core';

export const guard: CanActivateFn = async () => {
  const config = inject(Config);

  await config.loadConfigurations();

  return true;
};
