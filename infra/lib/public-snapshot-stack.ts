import { Stack, StackProps, RemovalPolicy, CfnOutput } from 'aws-cdk-lib/core';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';

/**
 * One S3 bucket holding one public object: the rain-alert cycle's snapshot
 * (`status.json`), read by the public status page.
 *
 * See design D-`public-status-page` and
 * `docs/runbooks/public-page-deploy.md` for the deploy procedure and the
 * verification steps this stack's policy is meant to satisfy.
 */
export class PublicSnapshotStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    const bucket = new s3.Bucket(this, 'PublicSnapshot', {
      // Amplify's reverse proxy fetches server-side and does NOT sign the
      // request, so the object must be publicly readable. The rewrite hides
      // it from the browser; it does not protect it. Acceptable because
      // this bucket holds one file whose contents are exactly what the page
      // displays, and nothing else ever goes in here.
      blockPublicAccess: new s3.BlockPublicAccess({
        blockPublicAcls: true,
        ignorePublicAcls: true,
        blockPublicPolicy: false,
        restrictPublicBuckets: false,
      }),

      // The one object every resident reads had no history. If the cycle's
      // credential leaks, a well-formed document reading "sin riesgo" during
      // a real flood renders faithfully, and without versioning there is no
      // prior copy to compare it against or roll back to.
      versioned: true,

      removalPolicy: RemovalPolicy.DESTROY,

      // `autoDeleteObjects` is deliberately NOT set. It provisions a Lambda
      // whose role is granted s3:PutBucketPolicy on this bucket, and
      // `blockPublicPolicy: false` above means S3 accepts whatever policy
      // that role writes. An attacker with a foothold on that Lambda grants
      // s3:PutObject to "*" and decides what every resident reads: an IAM
      // foothold becomes control of the alert page. It bought one manual
      // step on `cdk destroy` of a bucket holding one object.
      // `docs/runbooks/public-page-deploy.md` carries the manual empty step.
    });

    bucket.addToResourcePolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject'],
        principals: [new iam.AnyPrincipal()],
        // One key. Not the bucket.
        resources: [bucket.arnForObjects('status.json')],
      }),
    );

    // Who may WRITE. The stack used to define only who may read, which left
    // the write grant to be attached by hand after deploy — and a grant
    // invented at the console is broad, because narrowing it is work nobody
    // is prompted to do. Defined here instead, as a customer-managed policy
    // an operator attaches to whichever principal runs the cycle: no
    // principal is named because today that is a developer's session and in
    // change 3 it is a Lambda's role.
    //
    // s3:PutObject on one key. No DeleteObject: publishing replaces the
    // object, so a writer that can delete it can only make the page
    // unreadable. No s3:PutBucketPolicy, for the reason above.
    const writerPolicy = new iam.ManagedPolicy(this, 'SnapshotWriter', {
      description: 'Publish the rain-alert public snapshot: PutObject on status.json and nothing else',
      statements: [
        new iam.PolicyStatement({
          actions: ['s3:PutObject'],
          resources: [bucket.arnForObjects('status.json')],
        }),
      ],
    });

    new CfnOutput(this, 'SnapshotBucketName', { value: bucket.bucketName });
    new CfnOutput(this, 'SnapshotObjectUrl', { value: bucket.urlForObject('status.json') });
    new CfnOutput(this, 'SnapshotWriterPolicyArn', { value: writerPolicy.managedPolicyArn });
  }
}
