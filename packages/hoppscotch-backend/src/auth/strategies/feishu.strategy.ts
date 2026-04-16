import { Strategy as CustomStrategy } from 'passport-custom';
import { PassportStrategy } from '@nestjs/passport';
import { Injectable, UnauthorizedException } from '@nestjs/common';
import { AuthService } from '../auth.service';
import { UserService } from 'src/user/user.service';
import * as O from 'fp-ts/Option';
import * as E from 'fp-ts/Either';
import { ConfigService } from '@nestjs/config';
import { validateEmail } from 'src/utils';
import { AUTH_EMAIL_NOT_PROVIDED_BY_OAUTH } from 'src/errors';

@Injectable()
export class FeishuStrategy extends PassportStrategy(CustomStrategy, 'feishu') {
  constructor(
    private authService: AuthService,
    private usersService: UserService,
    private configService: ConfigService,
  ) {
    super();
  }

  async authenticate(req: any, options: any) {
    const appId = this.configService.get<string>('INFRA.FEISHU_CLIENT_ID');
    const appSecret = this.configService.get<string>('INFRA.FEISHU_CLIENT_SECRET');
    const callbackURL = this.configService.get<string>(
      'INFRA.FEISHU_CALLBACK_URL',
    );

    // Authorization phase: redirect to Feishu login page
    if (!req.query?.code) {
      // Feishu platform may validate callback URL without params
      if (!req.query.redirect_uri && req.path?.includes('/callback')) {
        if (req.res) {
          req.res.setHeader('Content-Type', 'application/json');
          req.res.end(JSON.stringify({ status: 'ok' }));
          return;
        }
      }

      const authUrl = new URL(
        'https://open.feishu.cn/open-apis/authen/v1/authorize',
      );
      authUrl.searchParams.set('app_id', appId);
      authUrl.searchParams.set('redirect_uri', callbackURL);
      const state = JSON.stringify({
        redirect_uri: req.query.redirect_uri || null,
      });
      authUrl.searchParams.set('state', state);

      return this.redirect(authUrl.toString());
    }

    // Callback phase: exchange code for tokens, then fetch user profile
    try {
      const code = req.query.code;

      // Step 1: Exchange authorization code for access_token (JSON body required by Feishu)
      const tokenRes = await fetch(
        'https://open.feishu.cn/open-apis/authen/v2/oauth/token',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            grant_type: 'authorization_code',
            code: code,
            client_id: appId,
            client_secret: appSecret,
            redirect_uri: callbackURL,
          }),
        },
      );

      const tokenData = await tokenRes.json();

      if (tokenData.code !== 0) {
        return this.error(
          new Error(`Feishu token error: ${tokenData.msg}`),
        );
      }

      const accessToken = tokenData.access_token;
      const refreshToken = tokenData.refresh_token;

      // Step 2: Fetch user info using the access_token
      const userRes = await fetch(
        'https://open.feishu.cn/open-apis/authen/v1/user_info',
        {
          headers: { Authorization: `Bearer ${accessToken}` },
        },
      );
      const userData = await userRes.json();

      if (userData.code !== 0) {
        return this.error(
          new Error(`Feishu user info error: ${userData.msg}`),
        );
      }

      const userInfo = userData.data;
      const email =
        userInfo.email && validateEmail(userInfo.email)
          ? userInfo.email
          : `${userInfo.open_id}@feishu.local`;
      const profile = {
        id: userInfo.open_id,
        displayName: userInfo.name || null,
        provider: 'feishu',
        emails: [{ value: email }],
        photos: [{ value: userInfo.avatar_url || null }],
      };

      // Step 3: Validate - find/create user, link provider account
      const validatedUser = await this.validate(accessToken, refreshToken, profile);

      // Set authInfo.state so Controller can read redirect_uri
      try {
        req.authInfo = {
          state: JSON.parse(decodeURIComponent(req.query.state || '{}')),
        };
      } catch {
        req.authInfo = { state: {} };
      }

      this.success(validatedUser);
    } catch (error) {
      console.error('[Feishu] Error:', error);
      this.error(error);
    }
  }

  private async validate(
    accessToken: string,
    refreshToken: string,
    profile: any,
  ) {
    const email = profile.emails?.[0]?.value;

    if (!validateEmail(email))
      throw new UnauthorizedException(AUTH_EMAIL_NOT_PROVIDED_BY_OAUTH);

    const user = await this.usersService.findUserByEmail(email);

    if (O.isNone(user)) {
      const createdUser = await this.usersService.createUserSSO(
        accessToken,
        refreshToken,
        profile,
      );
      return createdUser;
    }

    if (!user.value.displayName || !user.value.photoURL) {
      const updatedUser = await this.usersService.updateUserDetails(
        user.value,
        profile,
      );
      if (E.isLeft(updatedUser)) {
        throw new UnauthorizedException(updatedUser.left);
      }
    }

    const providerAccountExists =
      await this.authService.checkIfProviderAccountExists(user.value, profile);

    if (O.isNone(providerAccountExists))
      await this.usersService.createProviderAccount(
        user.value,
        accessToken,
        refreshToken,
        profile,
      );

    return user.value;
  }
}
