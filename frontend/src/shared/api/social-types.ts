import type { components as Social } from "./schema/social";
import type { components as Bff } from "./schema/bff_web";

type S = Social["schemas"];
type B = Bff["schemas"];

export type FriendView = S["FriendView"];
export type FriendList = S["FriendList"];
export type FriendStatus = FriendView["status"];
export type FriendRequestView = S["FriendRequestView"];
export type FriendRequests = S["FriendRequests"];
export type FriendCodeView = S["FriendCodeView"];
export type BlockView = S["BlockView"];
export type PrivacySettings = S["PrivacySettings"];
export type CommentView = S["CommentView"];
export type CommentPage = S["CommentPage"];
export type ReportReason = S["ReportReason"];
export type ReportKind = S["ReportCreate"]["target_kind"];
export type ReportResult = S["ReportResult"];
export type RoomInviteResult = S["RoomInviteResult"];
export type MySanction = S["MySanction"];
export type MySanctions = S["MySanctions"];
export type SanctionKind = MySanction["kind"];

export type CommentsCard = B["CommentsCard"];
export type CommentCard = B["CommentCard"];
export type FriendsCard = B["FriendsCard"];
export type PlayerSocialCard = B["PlayerSocialCard"];
export type SanctionsCard = B["SanctionsCard"];
