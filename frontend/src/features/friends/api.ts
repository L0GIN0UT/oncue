/**
 * Друзья, блокировки, приватность, приглашения, жалобы и санкции (этап 5c). Все вызовы идут
 * прямо в social через gateway (`/api/social/v1`, ADR 0017); bff только читает блоки страниц.
 * Мутации с побочным эффектом несут `Idempotency-Key`: вызывающий создаёт ключ на одно
 * действие пользователя и повторяет сетевую ошибку с тем же ключом.
 */
import { api, unwrap } from "@/shared/api/client";
import type {
  BlockView,
  CommentPage,
  CommentView,
  FriendCodeView,
  FriendList,
  FriendRequestView,
  FriendRequests,
  PrivacySettings,
  ReportKind,
  ReportResult,
  ReportReason,
  RoomInviteResult,
  MySanctions,
} from "@/shared/api/types";
import { newIdempotencyKey } from "@/shared/lib/id";
import { COMMENTS_PAGE_SIZE } from "./config";

export { newIdempotencyKey };

const idem = (key: string) => ({ header: { "Idempotency-Key": key } });

export const friendsKeys = {
  list: ["social", "friends"] as const,
  requests: ["social", "requests"] as const,
  blocks: ["social", "blocks"] as const,
  code: ["social", "code"] as const,
  privacy: ["social", "privacy"] as const,
  sanctions: ["social", "sanctions"] as const,
  reasons: ["social", "report-reasons"] as const,
};

// ------------------------------------------------------------------ друзья
export async function fetchFriends(): Promise<FriendList> {
  return unwrap(await api().social.GET("/v1/me/friends"));
}

export async function removeFriend(userId: string): Promise<void> {
  unwrap(
    await api().social.DELETE("/v1/me/friends/{user_id}", {
      params: { path: { user_id: userId } },
    }),
  );
}

export async function fetchRequests(): Promise<FriendRequests> {
  return unwrap(await api().social.GET("/v1/me/friend-requests"));
}

/** Заявка по нику или коду: ровно одно поле. */
export async function sendRequest(
  target: { nickname: string } | { code: string },
  key: string,
): Promise<FriendRequestView> {
  return unwrap(
    await api().social.POST("/v1/me/friend-requests", { params: idem(key), body: target }),
  );
}

export async function acceptRequest(id: string, key: string): Promise<void> {
  unwrap(
    await api().social.POST("/v1/me/friend-requests/{request_id}/accept", {
      params: { path: { request_id: id }, ...idem(key) },
    }),
  );
}

export async function declineRequest(id: string, key: string): Promise<void> {
  unwrap(
    await api().social.POST("/v1/me/friend-requests/{request_id}/decline", {
      params: { path: { request_id: id }, ...idem(key) },
    }),
  );
}

/** Отозвать свою исходящую заявку. */
export async function cancelRequest(id: string): Promise<void> {
  unwrap(
    await api().social.DELETE("/v1/me/friend-requests/{request_id}", {
      params: { path: { request_id: id } },
    }),
  );
}

export async function fetchMyCode(): Promise<FriendCodeView> {
  return unwrap(await api().social.GET("/v1/me/friend-code"));
}

export async function regenerateCode(key: string): Promise<FriendCodeView> {
  return unwrap(await api().social.POST("/v1/me/friend-code/regenerate", { params: idem(key) }));
}

// ------------------------------------------------------------------ блокировки и приватность
export async function fetchBlocks(): Promise<BlockView[]> {
  return unwrap(await api().social.GET("/v1/me/blocks")).items;
}

export async function blockPlayer(userId: string): Promise<void> {
  unwrap(
    await api().social.PUT("/v1/me/blocks/{user_id}", { params: { path: { user_id: userId } } }),
  );
}

export async function unblockPlayer(userId: string): Promise<void> {
  unwrap(
    await api().social.DELETE("/v1/me/blocks/{user_id}", {
      params: { path: { user_id: userId } },
    }),
  );
}

export async function fetchPrivacy(): Promise<PrivacySettings> {
  return unwrap(await api().social.GET("/v1/me/privacy"));
}

export async function savePrivacy(body: PrivacySettings): Promise<PrivacySettings> {
  return unwrap(await api().social.PUT("/v1/me/privacy", { body }));
}

// ------------------------------------------------------------------ присутствие и приглашения
export async function sendPresence(): Promise<void> {
  unwrap(await api().social.POST("/v1/me/presence"));
}

export async function inviteFriend(
  userId: string,
  roomCode: string,
  key: string,
): Promise<RoomInviteResult> {
  return unwrap(
    await api().social.POST("/v1/me/friends/{user_id}/invite", {
      params: { path: { user_id: userId }, ...idem(key) },
      body: { room_code: roomCode },
    }),
  );
}

// ------------------------------------------------------------------ комментарии
export async function fetchComments(
  collectionId: string,
  cursor: string | null,
): Promise<CommentPage> {
  return unwrap(
    await api().social.GET("/v1/collections/{collection_id}/comments", {
      params: {
        path: { collection_id: collectionId },
        query: { limit: COMMENTS_PAGE_SIZE, ...(cursor ? { cursor } : {}) },
      },
    }),
  );
}

export async function postComment(
  collectionId: string,
  body: string,
  key: string,
): Promise<CommentView> {
  return unwrap(
    await api().social.POST("/v1/collections/{collection_id}/comments", {
      params: { path: { collection_id: collectionId }, ...idem(key) },
      body: { body },
    }),
  );
}

export async function editComment(id: string, body: string): Promise<CommentView> {
  return unwrap(
    await api().social.PATCH("/v1/comments/{comment_id}", {
      params: { path: { comment_id: id } },
      body: { body },
    }),
  );
}

export async function deleteComment(id: string): Promise<void> {
  unwrap(
    await api().social.DELETE("/v1/comments/{comment_id}", {
      params: { path: { comment_id: id } },
    }),
  );
}

// ------------------------------------------------------------------ жалобы и санкции
export async function fetchReportReasons(): Promise<ReportReason[]> {
  return unwrap(await api().social.GET("/v1/report-reasons")).items;
}

export type ReportInput = {
  target_kind: ReportKind;
  target_id: string;
  reason_code: string;
  text?: string | null;
  context_match_id?: string | null;
};

export async function fileReport(input: ReportInput, key: string): Promise<ReportResult> {
  return unwrap(await api().social.POST("/v1/reports", { params: idem(key), body: input }));
}

export async function fetchMySanctions(): Promise<MySanctions> {
  return unwrap(await api().social.GET("/v1/me/sanctions"));
}
